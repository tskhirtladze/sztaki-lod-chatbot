import os
import re
import uuid
import json
import sqlite3
import requests
from collections import Counter
from typing import Annotated, TypedDict

from flask import Flask, render_template, request, jsonify, Response, session, stream_with_context, url_for
from langchain_ollama import ChatOllama
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
import ssl
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import io
import base64
import matplotlib
matplotlib.use("Agg")  # headless — this runs inside a Flask server process, no display
import matplotlib.pyplot as plt
from knowledge_graph import *


try:
    from langgraph.checkpoint.sqlite import SqliteSaver
except ModuleNotFoundError:
    from langgraph.checkpoint.memory import MemorySaver as SqliteSaver

app = Flask(__name__)

# ── CONFIG ────────────────────────────────────────────────────────────────────
FUSEKI_ENDPOINT = os.environ.get("FUSEKI_ENDPOINT", "https://lod.sztaki.hu/sparql")
FUSEKI_TIMEOUT  = int(os.environ.get("FUSEKI_TIMEOUT", "75"))
MAX_TOKENS      = int(os.environ.get("LLM_MAX_TOKENS", "1200"))
SECRET_KEY      = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))
DB_PATH         = os.environ.get("MEMORY_DB", "memory.db")

app.secret_key = SECRET_KEY

# ── LLM ───────────────────────────────────────────────────────────────────────
llm = ChatOllama(model="llama3.1", temperature=0, seed=42, num_predict=MAX_TOKENS)

_REQUIRED  = re.compile(r'\bSELECT\b.+\bWHERE\b.+\{', re.DOTALL | re.IGNORECASE)
_WRITE_OPS = re.compile(r'\b(INSERT|DELETE|DROP|CLEAR|LOAD|CREATE)\b', re.IGNORECASE)

def validate_sparql(query: str) -> tuple[bool, str]:
    if not query.strip():
        return False, "Empty query."
    if not _REQUIRED.search(query):
        return False, "Query must contain SELECT … WHERE { … }."
    if _WRITE_OPS.search(query):
        return False, "Only SELECT queries are allowed."
    return True, ""



def check_data_availability(user_query: str) -> dict:
    """
    Runs BEFORE any SPARQL is written. Asks the LLM whether the user's question
    maps to concepts that actually exist in this knowledge graph's schema —
    nothing else. This stops the app from ever generating a query for something
    the dataset simply doesn't track (weather, stock prices, unrelated topics).

    The LLM is only shown the schema, and is explicitly told to explain itself
    using ONLY the schema's own class/property names — it must not repeat or
    "validate" any keyword from the user's question that isn't part of that
    vocabulary, so an unavailable request doesn't come back sounding like an
    endorsement of whatever the user happened to ask for.
    """
    prompt = f"""{CURATED_SCHEMA}

{ENDPOINT_FACTS}

You are checking whether a user's question can be answered using ONLY the
classes and properties listed in the schema above — nothing else.

User question: "{user_query}"

Respond with ONLY a JSON object, no markdown, no extra text:
{{"available": true or false, "reason": "one short sentence"}}

Rules for "reason":
- Refer ONLY to class/property names that appear in the schema above
  (e.g. dcterms:subject, dbo:Work, dcmitype:Sound, foaf:name).
- NEVER repeat, name, or "confirm" any topic/entity from the user's question
  that is not itself part of the schema above.
- If unavailable, just state that the schema has no matching class or
  property — do not guess what the user might have meant.
"""
    try:
        raw = llm.invoke(prompt).content.strip()
        raw = re.sub(r"```[a-zA-Z]*", "", raw).replace("```", "").strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(match.group(0) if match else raw)
        return {
            "available": bool(parsed.get("available", True)),
            "reason": str(parsed.get("reason", ""))[:200],
        }
    except Exception as e:
        print(f"[AVAILABILITY CHECK ERROR] {e}")
        # Fail open — a parsing hiccup here shouldn't block a legitimate query
        return {"available": True, "reason": ""}


def _ensure_graph_scope(body: str) -> str:
    """
    Safety net: guarantee the query's WHERE body is wrapped in
    GRAPH <http://lod.sztaki.hu/nda> { ... }, regardless of whether the LLM
    actually included it. This endpoint hosts several unrelated named graphs
    that reuse the same property names as this dataset, so an unscoped query
    can silently mix in data from a totally different graph — this must never
    be allowed through even if the prompt instruction gets ignored.
    """
    match = re.search(r"\bWHERE\s*\{", body, re.IGNORECASE)
    if not match:
        return body  # can't locate a WHERE block — leave as-is, execute_sparql will reject it

    start = match.end()  # position right after the opening "{"
    # Already scoped? (allow leading whitespace before GRAPH)
    if re.match(r"\s*GRAPH\s+<", body[start:], re.IGNORECASE):
        return body

    # Find the matching closing brace via depth counting
    depth = 1
    i = start
    while i < len(body) and depth > 0:
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
        i += 1
    if depth != 0:
        return body  # unbalanced braces — leave as-is, execute_sparql will reject it
    inner_end = i - 1  # index of the matching "}"

    inner = body[start:inner_end]
    wrapped_inner = f" GRAPH <{NDA_GRAPH}> {{{inner}}} "
    return body[:start] + wrapped_inner + body[inner_end + 1:]


def resolve_entities(user_query: str) -> str:
    """
    Entity-resolution pass, run BEFORE SPARQL generation. Free-text SPARQL
    generation fails silently when the user names something in a different
    language or spelling than what's actually stored in the graph — e.g.
    "Hungarian Central Statistical Office" vs. the graph's real stored value
    "Központi Statisztikai Hivatal". Rather than trust the generator to guess
    the exact stored string, this:
      1. Asks the LLM for a few candidate search terms — including a
         Hungarian translation/transliteration guess, since names in this
         graph are almost entirely Hungarian.
      2. Runs a real, cheap regex lookup against the graph for each candidate,
         across every literal name-bearing property.
      3. Returns whichever candidates actually matched something real, with
         their exact stored string — THIS is what generate_sparql should
         search for, not the user's original wording.
    Returns grounding text to inject into the SPARQL prompt, or "" if nothing
    matched (generate_sparql then falls back to the user's own wording).
    """
    prompt = f"""The user asked: "{user_query}"

If this question names a specific entity (a person, organization, publisher,
or work title), list up to 3 short candidate search terms for how it might
actually be stored in a Hungarian cultural-heritage database — include a
Hungarian translation/transliteration guess if it has an obvious Hungarian
name, plus the original term as given.

Respond with ONLY a JSON array of strings, no markdown, e.g.
["Központi Statisztikai Hivatal", "Statisztikai Hivatal", "Central Statistical Office"]

If no specific named entity is mentioned, respond with exactly: []
"""
    try:
        raw = llm.invoke(prompt).content.strip()
        raw = re.sub(r"```[a-zA-Z]*", "", raw).replace("```", "").strip()
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        candidates = json.loads(match.group(0) if match else raw)
        candidates = [c.strip() for c in candidates if isinstance(c, str) and c.strip()][:3]
    except Exception as e:
        print(f"[ENTITY CANDIDATES ERROR] {e}")
        return ""

    if not candidates:
        return ""

    found = []
    for term in candidates:
        # Keep the regex literal safe — strip characters that would break out
        # of the quoted regex argument or have special regex meaning we don't want.
        safe_term = re.sub(r'["\\.*+?()\[\]{}|^$]', " ", term).strip()
        if not safe_term:
            continue
        lookup = SPARQL_PREFIXES + f"""
SELECT DISTINCT ?label WHERE {{
  GRAPH <{NDA_GRAPH}> {{
    {{ ?s foaf:name ?label }}
    UNION {{ ?s dcterms:alternative ?label }}
    UNION {{ ?s dcterms:title ?label }}
    UNION {{ ?s dcterms:publisher ?label }}
    FILTER(regex(?label, "{safe_term}", "i"))
  }}
}} LIMIT 5
"""
        rows = execute_sparql(lookup)
        if isinstance(rows, list):
            for row in rows:
                lbl = row.get("label")
                if lbl and lbl not in found:
                    found.append(lbl)

    if not found:
        return ""

    return ("ENTITY RESOLUTION — these exact strings were found already stored "
            "in the graph and match the user's question. Write your regex/exact "
            f"match against THESE strings, not the user's original wording: {found[:5]}")


def generate_sparql(user_query: str, entity_context: str = "", retry_hint: str = "") -> str:
    prompt = f"""{SPARQL_PREFIXES}

{CURATED_SCHEMA}

{ENDPOINT_FACTS}

{QUERY_EXAMPLES}

You are a SPARQL expert for the SZTAKI LOD knowledge graph.

Generate ONE valid SPARQL SELECT query for this question:
"{user_query}"
{("\n" + entity_context + "\n") if entity_context else ""}{("\nPREVIOUS ATTEMPT FAILED — " + retry_hint + "\n") if retry_hint else ""}
STRICT INSTRUCTIONS:
- ALWAYS write SELECT DISTINCT (never plain SELECT — data has duplicate triples)
- ALWAYS wrap the query body in GRAPH <http://lod.sztaki.hu/nda> {{ ... }} — this
  endpoint hosts other unrelated named graphs that reuse the same property names
- ONLY use classes and properties from the schema above
- Use dbo:Work to filter works
- Use dcterms:title for titles
- Use dcterms:date for dates
- Use foaf:name for author names via JOIN on dcterms:creator
- DO NOT invent properties
- NEVER use BIND(), CONTAINS(), STRSTARTS(), LCASE(), SUBSTR() — this endpoint is SPARQL 1.0 only
- PREFER exact matching over filtering: for known value sets (content type, language) use
  an exact triple pattern or equality (?type = dcmitype:Sound), not a FILTER at all
- For multi-valued properties (format, type, identifier) or year filtering, fetch the
  property unfiltered with OPTIONAL and let the application split/compare the values —
  do NOT add a FILTER just to isolate one of the values
- ONLY use regex() as a last-resort fallback, and only for genuine free-text keyword
  search on a user-supplied term (title/subject/description/author name substring
  search) where no exact-match alternative exists: regex(?var, "keyword", "i")


OUTPUT RULES:
- ONLY the raw SPARQL query
- No markdown, no explanation
- Start directly with SELECT DISTINCT (do NOT include PREFIX lines — added automatically)
- End with LIMIT 15
"""
    raw = llm.invoke(prompt).content.strip()

    # Strip markdown fences
    raw = re.sub(r"```[a-zA-Z]*", "", raw).replace("```", "").strip()

    # Strip ALL prefix/define lines the LLM may have output
    # This handles PREFIX, define sql:, #comment lines at the top
    lines = raw.splitlines()
    clean_lines = []
    skip_header = True
    for line in lines:
        stripped = line.strip()
        if skip_header:
            if (stripped.upper().startswith("PREFIX") or
                stripped.startswith("define ") or
                stripped.startswith("#")):
                continue  # drop these lines
            else:
                skip_header = False  # first real line reached
        clean_lines.append(line)

    body = "\n".join(clean_lines).strip()

    # Safety fallback — if body is empty or has no SELECT, use a default query
    if not body or "SELECT" not in body.upper():
        body = f"""SELECT DISTINCT ?item ?title WHERE {{
  GRAPH <{NDA_GRAPH}> {{
    ?item rdf:type dbo:Work ;
          dcterms:title ?title .
  }}
}} LIMIT 15"""

    # Safety net — guarantee graph scoping even if the LLM forgot it
    body = _ensure_graph_scope(body)

    return SPARQL_PREFIXES + "\n\n" + body


def execute_sparql(sparql: str) -> list | str:
    ok, err = validate_sparql(sparql)
    if not ok:
        print(f"[SPARQL VALIDATION ERROR] {err}")
        return f"VALIDATION_ERROR: {err}"
    try:
        print(f"[SPARQL QUERY]\n{sparql}\n")
        resp = requests.get(
            FUSEKI_ENDPOINT,
            params={"query": sparql},
            headers={"Accept": "application/sparql-results+json"},
            timeout=FUSEKI_TIMEOUT,
            verify=False,
        )
        print(f"[HTTP STATUS] {resp.status_code}")
        print(f"[RAW RESPONSE] {resp.text[:1000]}")  # first 1000 chars

        if resp.status_code != 200:
            return f"DATABASE_ERROR: HTTP {resp.status_code}"
        bindings = resp.json().get("results", {}).get("bindings", [])
        print(f"[BINDINGS COUNT] {len(bindings)}")
        if not bindings:
            return "DATABASE_EMPTY"
        return [{k: v["value"] for k, v in row.items()} for row in bindings[:15]]
    except requests.exceptions.ConnectionError as e:
        print(f"[CONNECTION ERROR] {e}")
        return f"ENDPOINT_UNREACHABLE: Cannot connect to {FUSEKI_ENDPOINT}"
    except requests.exceptions.Timeout:
        print(f"[TIMEOUT]")
        return f"TIMEOUT: No response after {FUSEKI_TIMEOUT}s"
    except Exception as e:
        print(f"[SYSTEM ERROR] {e}")
        return f"SYSTEM_ERROR: {e}"


# ── VISUALIZATION ───────────────────────────────────────────────────────────
def _is_numeric(val) -> bool:
    try:
        float(val)
        return True
    except Exception:
        return False


def _clean_label(val, fallback: str) -> str:
    """Turn a raw cell value into a short, readable chart label."""
    lbl = str(val) if val not in (None, "") else fallback
    if lbl.startswith("http"):
        lbl = lbl.rstrip("/").split("/")[-1].replace("_", " ")
    return lbl[:50]


def _plan_visualization(data: list, user_query: str, keys: list) -> dict | None:
    """
    Step 3 of the pipeline: after the SPARQL results come back, ask the LLM to
    decide HOW to visualize them — whether a chart is even worth showing, what
    chart type fits, which column is the category axis, which is the numeric
    axis, and (step 4) a short caption describing what the chart shows.

    Returns None if the LLM says a chart wouldn't add anything, or if its
    answer can't be trusted (bad JSON, columns that don't actually exist).
    """
    sample = data[:8]
    prompt = f"""You are deciding how to visualize the results of a knowledge-graph query.

User question: "{user_query}"

Result columns: {keys}
Sample rows (JSON): {json.dumps(sample, ensure_ascii=False)}

If a chart would meaningfully help answer the question, respond with ONLY this
JSON object (no markdown, no extra text):
{{"visualize": true, "chart_type": "bar" or "pie" or "line",
  "label_column": "<one of the result columns above>",
  "value_column": "<one of the result columns above holding real numbers, OR the literal string \\"COUNT\\">",
  "description": "one short plain-English sentence describing what the chart shows"}}

Most result sets are one row per item (e.g. one row per work) with no numeric
column at all — that's normal, not a reason to skip the chart. In that case set
value_column to the literal string "COUNT" and pick whichever label_column would
group the rows into a meaningful breakdown (e.g. count of works per publisher,
per content type, per year) — NOT a column where almost every row is unique
(e.g. title, or a full timestamp), since that produces a flat, uninformative
chart. Only use an actual column name for value_column when the data already
contains real numbers to plot.

If the data is not meaningfully chartable (e.g. a plain text list with no
numeric or countable dimension), respond with ONLY:
{{"visualize": false}}
"""
    try:
        raw = llm.invoke(prompt).content.strip()
        raw = re.sub(r"```[a-zA-Z]*", "", raw).replace("```", "").strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        plan = json.loads(match.group(0) if match else raw)
    except Exception as e:
        print(f"[VIZ PLAN ERROR] {e}")
        return None

    if not plan.get("visualize"):
        return None
    if plan.get("label_column") not in keys:
        return None  # LLM hallucinated a column that isn't actually in the results
    if plan.get("value_column") != "COUNT" and plan.get("value_column") not in keys:
        return None  # same, for the value column — unless it's the COUNT sentinel
    if plan.get("chart_type") not in ("bar", "pie", "line"):
        plan["chart_type"] = "bar"
    return plan


def _best_count_column(data: list, keys: list) -> str | None:
    """
    Pick the categorical column that produces the most meaningful COUNT
    breakdown, for use when no numeric column exists at all (the common case —
    one row per item, e.g. one row per work).

    Skips columns where every value is unique (e.g. title, a full timestamp) —
    those produce a flat "1 each" chart that says nothing. Prefers columns with
    fewer distinct buckets relative to the row count, since that's what makes a
    COUNT breakdown actually informative (e.g. 13 works split 11/2 across two
    publishers, rather than 13 works each on their own distinct date).
    """
    best_key, best_score = None, 0
    for k in keys:
        vals = [row.get(k) for row in data if row.get(k) not in (None, "")]
        if not vals:
            continue
        counts = Counter(_clean_label(v, "") for v in vals)
        distinct = len(counts)
        if distinct < 2 or distinct == len(vals):
            continue  # one bucket only, or every value is unique — no real repeats
        score = len(vals) / distinct
        if score > best_score:
            best_key, best_score = k, score
    return best_key


def _render_matplotlib_chart(labels: list, values: list, chart_type: str, title: str) -> str:
    """Render the chart with Matplotlib and return it as a base64 PNG data URI."""
    fig, ax = plt.subplots(figsize=(7.5, 4.5), dpi=130)

    if chart_type == "pie":
        ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90,
               textprops={"fontsize": 8})
        ax.axis("equal")
    elif chart_type == "line":
        ax.plot(labels, values, marker="o", color="#4d9fff")
        ax.set_ylabel("Value")
        plt.setp(ax.get_xticklabels(), rotation=40, ha="right", fontsize=8)
    else:  # bar — go horizontal for many/long labels so they stay readable
        horizontal = len(labels) > 6 or max((len(l) for l in labels), default=0) > 14
        if horizontal:
            ax.barh(labels, values, color="#f0c040")
            ax.invert_yaxis()
        else:
            ax.bar(labels, values, color="#f0c040")
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right", fontsize=8)

    ax.set_title(title, fontsize=11, fontweight="bold")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)  # always free the figure — this runs inside a live server
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def try_build_visualization(data: list, user_query: str) -> dict | None:
    """
    Decide whether the retrieved rows are worth charting, and if so, render an
    actual image with Matplotlib — not just a chart-library config blob.

    Flow:
      1. Cheap guard rail — need a list of at least 2 rows to chart anything.
      2. Ask the LLM which columns to use and whether a chart is worth it at
         all (_plan_visualization). Falls back to a simple numeric-column
         heuristic only when there's an unambiguous numeric/aggregate column,
         so genuinely non-chartable text lists still return None.
      3. Build clean label/value arrays from the columns chosen.
      4. Render with Matplotlib → base64 PNG.
      5. Package the image together with the LLM's short description.
    """
    if not isinstance(data, list) or len(data) < 2:
        return None

    keys = list({k for row in data for k in row.keys()})
    plan = _plan_visualization(data, user_query, keys)

    if plan is None:
        numeric_keys = [
            k for k in keys
            if sum(1 for r in data if _is_numeric(r.get(k))) >= max(1, len(data) // 2)
        ]
        aggregate_keys = [k for k in keys if k.lower() in
                           ("count", "total", "score", "num", "amount", "freq", "n")]
        value_key = (numeric_keys or aggregate_keys or [None])[0]
        if value_key:
            label_key = next((k for k in keys if k != value_key), value_key)
            plan = {
                "chart_type": "bar",
                "label_column": label_key,
                "value_column": value_key,
                "description": f"Shows {value_key} broken down by {label_key}.",
            }
        else:
            # No numeric column at all — the common case, one row per item.
            # Fall back to counting rows per category on whichever column
            # actually groups meaningfully, instead of giving up entirely.
            count_key = _best_count_column(data, keys)
            if not count_key:
                return None  # nothing numeric AND nothing groupable — truly not chartable
            plan = {
                "chart_type": "bar",
                "label_column": count_key,
                "value_column": "COUNT",
                "description": f"Shows how many results share each {count_key}.",
            }

    label_key   = plan["label_column"]
    value_key   = plan["value_column"]
    chart_type  = plan.get("chart_type", "bar")
    description = plan.get("description") or f"Shows {value_key} by {label_key}."

    if value_key == "COUNT":
        counts = Counter(
            _clean_label(row.get(label_key), f"Item {i+1}") for i, row in enumerate(data)
        )
        # Cap to the top buckets so the chart stays readable if there are many
        top = counts.most_common(15)
        labels = [k for k, _ in top]
        values = [float(v) for _, v in top]
        title = f"Count by {label_key}"
    else:
        labels, values = [], []
        for i, row in enumerate(data):
            try:
                val = float(row.get(value_key))
            except Exception:
                continue
            labels.append(_clean_label(row.get(label_key), f"Item {i+1}"))
            values.append(val)
        title = f"{label_key} · {value_key}"

    if len(labels) < 2:
        return None
    try:
        image = _render_matplotlib_chart(labels, values, chart_type, title)
    except Exception as e:
        print(f"[MATPLOTLIB RENDER ERROR] {e}")
        return None

    return {
        "is_visual":   True,
        "type":        chart_type,
        "title":       title,
        "description": description,   # short caption, per requirement 4
        "image":       image,         # data:image/png;base64,... — render with <img>
        "labels":      labels,
        "values":      values,
    }


# ══════════════════════════════════════════════════════════════════════════════
# LANGGRAPH STATE + GRAPH
# ══════════════════════════════════════════════════════════════════════════════

# ── State ───────────────────────────────────────────────────

class ResearchState(TypedDict):
    # Conversation history — accumulated with the add_messages reducer
    messages:    Annotated[list[BaseMessage], add_messages]
    # Filled by kg_query_node
    sparql_query: str | None
    raw_results:  list | str | None
    # Filled by visualize_node
    viz:          dict | None

# ── Node: knowledge-graph query ──────────
# No more intent classification / casual-chat branch — every message goes
# straight through the knowledge-graph pipeline. There is nothing for this
# app to say that isn't grounded in a real (attempted) query against the
# schema, so a chit-chat shortcut that could assert unverified "facts" is
# no longer on the table.
def kg_query_node(state: ResearchState) -> ResearchState:
    """Check the question is grounded in the schema → resolve entities → generate SPARQL → execute (with one bounded retry on empty) → store raw results."""
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        ""
    )

    # Step 2 of the pipeline: recognize whether the data even exists in this
    # graph BEFORE spending an LLM call writing SPARQL for it.
    availability = check_data_availability(last_human)
    if not availability["available"]:
        reason = availability["reason"] or "The schema has no matching class or property for that."
        return {"sparql_query": None, "raw_results": f"NOT_IN_SCHEMA: {reason}"}

    # Entity resolution — ground any named entity in the question against
    # what's actually stored in the graph before generation, rather than
    # trusting the generator to guess the right language/spelling.
    entity_context = resolve_entities(last_human)

    sparql  = generate_sparql(last_human, entity_context)
    results = execute_sparql(sparql)

    # Bounded retry (one extra attempt only): an empty result on the first
    # try is often just a too-narrow query (wrong property, wrong wording),
    # not genuine absence of data. Ask once for a broader approach before
    # reporting nothing found.
    if results == "DATABASE_EMPTY":
        retry_sparql = generate_sparql(
            last_human, entity_context,
            retry_hint=("Your previous query returned zero results. Try a genuinely "
                        "different approach this time: search a different property, "
                        "drop an overly-specific filter, or broaden the keyword match. "
                        f"Previous query was: {sparql}")
        )
        retry_results = execute_sparql(retry_sparql)
        if isinstance(retry_results, list):
            return {"sparql_query": retry_sparql, "raw_results": retry_results}
        # retry also came back empty/failed — keep the original attempt, it's
        # no worse and avoids masking a genuinely-empty result with a second error

    return {"sparql_query": sparql, "raw_results": results}

# ── Node: visualize ───────────────────────
def visualize_node(state: ResearchState) -> ResearchState:
    """Decide whether the results are visualizable and build a chart config."""
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        ""
    )
    results = state.get("raw_results")
    viz = try_build_visualization(results, last_human) if isinstance(results, list) else None
    return {"viz": viz}

# ── Node: summarize ────────────────────────────────────────
def summarize_node(state: ResearchState) -> ResearchState:
    """Turn raw SPARQL results into a natural-language answer."""
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        ""
    )
    results = state.get("raw_results")

    if isinstance(results, str):
        if "DATABASE_EMPTY" in results:
            context = "The database returned no results for this query."
        elif results.startswith("NOT_IN_SCHEMA:"):
            # Covers both genuinely out-of-scope questions AND greetings/chit-chat,
            # since there's no separate casual-chat path anymore. The prompt below
            # asks the model to word this invitingly rather than like an error.
            context = ("This question doesn't map to anything in this knowledge "
                       f"graph's schema. {results.split(':', 1)[1].strip()}")
        else:
            context = f"The query failed: {results}"
    else:
        clean = [
            {k: (v.split("/")[-1].replace("_", " ") if str(v).startswith("http") else v)
             for k, v in row.items()}
            for row in results
        ]
        context = json.dumps(clean, ensure_ascii=False, indent=2)

    viz = state.get("viz")
    chart_instruction = (
        "A chart has already been generated from this data and will be shown "
        "directly below your answer as an actual image. Do NOT describe, draw, "
        "or narrate the chart yourself — no ASCII/markdown tables standing in "
        "for it, no 'here is a bar chart showing...', and no disclaimers about "
        "it not being displayed. Just summarize the findings in prose; the "
        "image speaks for itself."
        if viz else
        "No chart was generated for this answer. Do NOT claim you made one, "
        "describe what a chart 'would' look like, or apologize for a missing "
        "chart — just answer with the facts in plain prose."
    )

    prompt = f"""You are a helpful assistant for the SZTAKI LOD Hungarian cultural heritage database.
This assistant ONLY answers questions using this knowledge graph — it does not do
general chit-chat. If the message was a greeting or off-topic, say briefly and
warmly that you're here to help explore this knowledge graph specifically, and
give one or two examples of things you can look up (e.g. works by an author,
radio shows from a given year, works by content type).

User question: {last_human}

Query results from the knowledge graph:
{context}

{chart_instruction}

Write a clear, concise answer in plain English.
- Summarise what was found (titles, dates, counts, etc.)
- If no results, say so politely
- Do NOT mention SPARQL, JSON, or technical terms
- Do NOT invent data
- Keep it under 120 words
"""
    response = llm.invoke(prompt)
    return {"messages": [response]}

# ── Build the StateGraph ─────────────────────────────────────────────
def build_graph(checkpointer):
    g = StateGraph(ResearchState)

    # Add nodes
    g.add_node("kg_query",    kg_query_node)
    g.add_node("visualize",   visualize_node)
    g.add_node("summarize",   summarize_node)

    # Linear pipeline — every message: query → visualize → summarize → END
    g.add_edge(START,         "kg_query")
    g.add_edge("kg_query",    "visualize")
    g.add_edge("visualize",   "summarize")
    g.add_edge("summarize",   END)

    return g.compile(checkpointer=checkpointer)

# ── Instantiate with SqliteSaver ─────────────────────────────────────
_sqlite_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_checkpointer = SqliteSaver(_sqlite_conn)
langgraph_app = build_graph(_checkpointer)

# ══════════════════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    if "thread_id" not in session:
        session["thread_id"] = str(uuid.uuid4())
    return render_template("index.html")


@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    user_input = (request.json or {}).get("message", "").strip()
    if not user_input:
        return jsonify({"error": "Empty message"}), 400

    thread_id = session.get("thread_id", "default")
    config    = {"configurable": {"thread_id": thread_id}}

    def generate():
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        try:
            final_state = None

            for chunk in langgraph_app.stream(
                {"messages": [HumanMessage(content=user_input)]},
                config=config,
                stream_mode="updates",
            ):
                for node_name, update in chunk.items():
                    # Send status SSE for each node that fires
                    label = {
                        "kg_query":    "Querying knowledge graph…",
                        "visualize":   "Analysing results…",
                        "summarize":   "Writing summary…",
                    }.get(node_name, f"Running {node_name}…")

                    yield sse("status", {"text": label})
                    final_state = update   # keep last meaningful update

            # Get the final full state
            full_state = langgraph_app.get_state(config)
            state_vals = full_state.values if full_state else {}

            # Extract the last AI message
            messages = state_vals.get("messages", [])
            last_ai  = next(
                (m.content for m in reversed(messages) if isinstance(m, AIMessage)),
                "No response generated."
            )

            yield sse("done", {
                "response":    last_ai,
                "sparql_query": state_vals.get("sparql_query"),
                "viz":          state_vals.get("viz"),
            })

        except Exception as e:
            yield sse("error", {"text": str(e)})

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/chat", methods=["POST"])
def chat():
    """Non-streaming fallback."""
    user_input = (request.json or {}).get("message", "").strip()
    thread_id  = session.get("thread_id", "default")
    config     = {"configurable": {"thread_id": thread_id}}

    try:
        langgraph_app.invoke(
            {"messages": [HumanMessage(content=user_input)]},
            config=config,
        )
        full_state = langgraph_app.get_state(config)
        state_vals = full_state.values if full_state else {}
        messages   = state_vals.get("messages", [])
        last_ai    = next(
            (m.content for m in reversed(messages) if isinstance(m, AIMessage)),
            "No response."
        )
        return jsonify({
            "response":     last_ai,
            "sparql_query": state_vals.get("sparql_query"),
            "viz":          state_vals.get("viz"),
        })
    except Exception as e:
        return jsonify({"response": str(e)}), 500


@app.route("/reset", methods=["POST"])
def reset():
    old = session.get("thread_id")
    session["thread_id"] = str(uuid.uuid4())
    if old:
        try:
            _sqlite_conn.execute("DELETE FROM checkpoints WHERE thread_id=?", (old,))
            _sqlite_conn.commit()
        except Exception:
            pass
    return jsonify({"status": "ok"})


@app.route("/graph")
def graph():
    return render_template("graph.html")


@app.route("/graph/data")
def graph_data():
    """Fetch ALL nodes and edges from the SZTAKI graph."""
    query = SPARQL_PREFIXES + f"""
    SELECT DISTINCT ?s ?p ?o
    WHERE {{
      GRAPH <{NDA_GRAPH}> {{
        # Fetch all triples where the subject is a work or person
        {{
          ?s a dbo:Work .
          ?s ?p ?o .
        }} UNION {{
          ?s a foaf:Person .
          ?s ?p ?o .
        }} UNION {{
          # Fetch all triples where the object is a work or person
          ?s ?p ?work .
          ?work a dbo:Work .
        }} UNION {{
          ?s ?p ?person .
          ?person a foaf:Person .
        }}
      }}
    }}
    LIMIT 10000
    """

    def sparql_query(q):
        try:
            r = requests.get(
                FUSEKI_ENDPOINT,
                params={"query": q},
                headers={"Accept": "application/sparql-results+json"},
                timeout=FUSEKI_TIMEOUT,
                verify=False,
            )
            if r.status_code == 200:
                return r.json().get("results", {}).get("bindings", [])
        except Exception as e:
            print(f"SPARQL query failed: {e}")  # Debugging
            pass
        return []

    bindings = sparql_query(query)
    print(f"Fetched {len(bindings)} triples from SPARQL endpoint.")  # Debugging

    TYPE_MAPPINGS = {
        "http://dbpedia.org/ontology/Work": "work",
        "http://schema.org/CreativeWork": "work",
        "http://schema.org/Thing": "work",
        "http://xmlns.com/foaf/0.1/Person": "person",
        "http://dbpedia.org/ontology/Person": "person",
        "http://schema.org/Person": "person",
        "http://purl.org/dc/dcmitype/Sound": "sound",
        "http://purl.org/dc/dcmitype/Text": "other",
        "http://purl.org/dc/dcmitype/Image": "other",
        "http://purl.org/dc/dcmitype/MovingImage": "other",
        "http://www.w3.org/2004/02/skos/core#Concept": "concept",
    }

    EDGE_LABELS = {
        "creator": "created by",
        "type": "has type",
        "isPartOf": "part of",
        "subject": "about",
        "sameAs": "same as",
    }

    nodes = {}
    edges = []
    subject_nodes = {}  # Track subject literals as nodes

    def add_node(nid, ntype="other", label=None, props=None):
        if nid not in nodes:
            nodes[nid] = {
                "id": nid,
                "type": ntype,
                "label": label or nid.split("/")[-1],
                "props": props or {}
            }

    def add_edge(s, p, t):
        rel = p.split("/")[-1]
        edges.append({
            "s": s,
            "rel": EDGE_LABELS.get(rel, rel),
            "t": t
        })

    for row in bindings:
        s = row["s"]["value"]
        p = row["p"]["value"]
        o = row["o"]["value"]

        # Add subject node (always)
        add_node(s)

        # Handle literals (e.g., titles, names, dates)
        if o.startswith('"'):
            prop_name = p.split("/")[-1]
            if "props" not in nodes[s]:
                nodes[s]["props"] = {}
            nodes[s]["props"][prop_name] = o.strip('"')
            # Use title/name/label as the node label
            if prop_name in ["title", "name", "label", "alternative"]:
                nodes[s]["label"] = o.strip('"')
            # Special case: dcterms:subject (treat as a concept node)
            if p == "http://purl.org/dc/terms/subject":
                subject_value = o.strip('"')
                if subject_value:
                    subject_id = f"concept_{abs(hash(subject_value)) % 100000}"
                    add_node(subject_id, "concept", subject_value, {"scheme": "NDA Hungary"})
                    add_edge(s, p, subject_id)
        else:
            # Add object node (if it's a URI)
            add_node(o)
            # Add edge
            add_edge(s, p, o)
            # Handle rdf:type
            if p == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type":
                nodes[s]["type"] = TYPE_MAPPINGS.get(o, "other")
            # Handle dcterms:isPartOf (series)
            if p == "http://purl.org/dc/terms/isPartOf":
                nodes[o]["type"] = "series"
                nodes[o]["label"] = o.rstrip("/").split("/")[-1].replace("_", " ")

    print(f"Built {len(nodes)} nodes and {len(edges)} edges.")  # Debugging
    return jsonify({
        "nodes": list(nodes.values()),
        "edges": edges,
    })


@app.route("/sparql")
def sparql_explorer():
    return render_template('sparql.html')


if __name__ == "__main__":
    app.run(debug=True, port=5001)