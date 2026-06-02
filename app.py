import os
import re
import uuid
import json
import sqlite3
import requests
from typing import Annotated, TypedDict, Literal

from flask import Flask, render_template, request, jsonify, Response, session, stream_with_context, url_for
from langchain_ollama import ChatOllama
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
import ssl
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
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



def generate_sparql(user_query: str) -> str:
    prompt = f"""{SPARQL_PREFIXES}

{CURATED_SCHEMA}

{ENDPOINT_FACTS}

{QUERY_EXAMPLES}

You are a SPARQL expert for the SZTAKI LOD knowledge graph.

Generate ONE valid SPARQL SELECT query for this question:
"{user_query}"

STRICT INSTRUCTIONS:
- ALWAYS write SELECT DISTINCT (never plain SELECT — data has duplicate triples)
- ONLY use classes and properties from the schema above
- Use dbo:Work to filter works
- Use dcterms:title for titles
- Use dcterms:date for dates
- Use foaf:name for author names via JOIN on dcterms:creator
- Use FILTER(CONTAINS(LCASE(?var), "keyword")) for text search
- DO NOT invent properties
- Use regex(?var, "keyword", "i") for text search — NOT CONTAINS(), NOT LCASE(), NOT STRSTARTS()
- Use regex(?date, "^2003") for year filtering — NOT CONTAINS()
- NEVER use BIND(), CONTAINS(), STRSTARTS(), LCASE(), SUBSTR() — this endpoint is SPARQL 1.0 only


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
        body = """SELECT DISTINCT ?item ?title WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:title ?title .
} LIMIT 15"""

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


# ── VISUALIZATION ─────────
def _is_numeric(val) -> bool:
    try:
        float(val)
        return True
    except Exception:
        return False


def try_build_visualization(data: list, user_query: str) -> dict | None:
    if not isinstance(data, list) or len(data) < 2:
        return None

    keys = list({k for row in data for k in row.keys()})
    numeric_keys = [
        k for k in keys
        if sum(1 for r in data if _is_numeric(r.get(k))) >= max(1, len(data) // 2)
    ]
    label_keys = [k for k in keys if k not in numeric_keys]

    # ── No numeric column → only chart if there's an explicit aggregate field ──
    if not numeric_keys:
        aggregate_keys = [
            k for k in keys
            if k.lower() in ('count', 'total', 'score', 'num', 'amount', 'freq', 'n')
        ]
        if not aggregate_keys:
            return None  # pure text list - nothing meaningful to chart

        value_key = aggregate_keys[0]
        label_key = next(
            (k for k in label_keys if k != value_key),
            label_keys[0] if label_keys else value_key
        )
        labels, values, hover = [], [], []
        for i, row in enumerate(data):
            try:
                val = float(row.get(value_key, 0))
            except Exception:
                continue
            lbl = str(row.get(label_key, f"Item {i+1}"))
            if lbl.startswith("http"):
                lbl = lbl.rstrip("/").split("/")[-1].replace("_", " ")
            parts = []
            for k, v in row.items():
                v_str = str(v)
                if not v_str.startswith("http"):
                    parts.append(f"{k}: {v_str[:60]}")
                else:
                    parts.append(f"{k}: .../{v_str.rstrip('/').split('/')[-1]}")
            labels.append(lbl[:50])
            values.append(val)
            hover.append(" | ".join(parts))

        if len(labels) < 2:
            return None

        return {
            "is_visual": True, "type": "bar", "indexAxis": "y",
            "labels": labels, "values": values, "hover": hover,
            "title": f"{label_key} · {value_key}",
        }

    # ── Numeric column present ──────────────────────────────────────────────
    value_key = numeric_keys[0]
    preferred_labels = ["title", "name", "label", "authorname", "type",
                        "subject", "publisher", "series"]
    label_key = next(
        (k for p in preferred_labels for k in label_keys if p in k.lower()),
        label_keys[0] if label_keys else value_key
    )

    labels, values, hover = [], [], []
    for i, row in enumerate(data):
        try:
            val = float(row.get(value_key))
        except Exception:
            continue
        lbl = str(row.get(label_key, f"Item {i+1}"))
        if lbl.startswith("http"):
            lbl = lbl.rstrip("/").split("/")[-1].replace("_", " ")
        parts = []
        for k, v in row.items():
            v_str = str(v)
            if not v_str.startswith("http"):
                parts.append(f"{k}: {v_str[:60]}")
            else:
                parts.append(f"{k}: .../{v_str.rstrip('/').split('/')[-1]}")
        labels.append(lbl[:50])
        values.append(val)
        hover.append(" | ".join(parts))

    if len(labels) < 2:
        return None

    q = user_query.lower()
    chart_type = "pie" if "pie" in q else "bar"
    index_axis = "y"   if "horizontal" in q else ("x" if chart_type == "bar" else "x")
    return {
        "is_visual": True, "type": chart_type, "indexAxis": index_axis,
        "labels": labels, "values": values, "hover": hover,
        "title": f"{label_key} · {value_key}",
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
    # Routing flag set by the router
    needs_data:   bool

# ── Intent detection  ───────────────────────
_DATA_KW = re.compile(
    r'\b(list|show|find|get|fetch|give|display|how many|count|what|which|who|'
    r'books?|movies?|films?|articles?|authors?|subjects?|topics?|titles?|dates?|'
    r'years?|types?|items?|resources?|collections?|chart|graph|visual|bar|pie|'
    r'radio|shows?|audio|sound|photo|image|photos?|images?|series|publication|'
    r'hungarian|sztaki|database|sparql|linked|creator|publisher|language|format)\b',
    re.IGNORECASE
)

def classify_node(state: ResearchState) -> ResearchState:
    """ A node that sets a routing flag in state."""
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        ""
    )
    return {"needs_data": bool(_DATA_KW.search(last_human)), "sparql_query": None,
            "raw_results": None, "viz": None}

# ── Router ─────────────────────────────────────────
def route_after_classify(state: ResearchState) -> Literal["kg_query", "casual_chat"]:
    """Conditional edge function — returns the name of the next node."""
    return "kg_query" if state["needs_data"] else "casual_chat"

# ── Node: casual chat ────────────────────────────────────────────────
def casual_chat_node(state: ResearchState) -> ResearchState:
    """Simple LLM reply for greetings and off-topic messages."""
    messages = [
        SystemMessage(content=(
            "You are a helpful assistant for the SZTAKI LOD Hungarian cultural heritage "
            "knowledge graph. For casual greetings reply briefly. If the user seems to "
            "want data, invite them to ask a specific question about books, movies, articles, "
            "or authors."
        ))
    ] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response]}

# ── Node: knowledge-graph query ──────────
def kg_query_node(state: ResearchState) -> ResearchState:
    """Generate SPARQL → execute → store raw results."""
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        ""
    )
    sparql  = generate_sparql(last_human)
    results = execute_sparql(sparql)
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
        context = (
            "The database returned no results for this query."
            if "DATABASE_EMPTY" in results
            else f"The query failed: {results}"
        )
    else:
        clean = [
            {k: (v.split("/")[-1].replace("_", " ") if str(v).startswith("http") else v)
             for k, v in row.items()}
            for row in results
        ]
        context = json.dumps(clean, ensure_ascii=False, indent=2)

    prompt = f"""You are a helpful assistant for the SZTAKI LOD Hungarian cultural heritage database.

User question: {last_human}

Query results from the knowledge graph:
{context}

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
    g.add_node("classify",    classify_node)
    g.add_node("casual_chat", casual_chat_node)
    g.add_node("kg_query",    kg_query_node)
    g.add_node("visualize",   visualize_node)
    g.add_node("summarize",   summarize_node)

    # Edges
    g.add_edge(START, "classify")

    # Conditional edge — routes to kg_query OR casual_chat
    g.add_conditional_edges(
        "classify",
        route_after_classify,
        {"kg_query": "kg_query", "casual_chat": "casual_chat"}
    )

    # Data path: query → visualize → summarize → END
    g.add_edge("kg_query",    "visualize")
    g.add_edge("visualize",   "summarize")
    g.add_edge("summarize",   END)

    # Casual path: direct to END
    g.add_edge("casual_chat", END)

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
                        "classify":    "Classifying intent…",
                        "kg_query":    "Querying knowledge graph…",
                        "visualize":   "Analysing results…",
                        "summarize":   "Writing summary…",
                        "casual_chat": "Thinking…",
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
    """Fetch real nodes and edges from the SPARQL endpoint for the graph view."""

    # Query 1: sample works with properties
    works_sparql = SPARQL_PREFIXES + """
SELECT DISTINCT ?item ?title ?publisher ?date ?format WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:title ?title .
  OPTIONAL { ?item dcterms:publisher ?publisher . }
  OPTIONAL { ?item dcterms:date ?date . }
  OPTIONAL { ?item dcterms:format ?format .
             FILTER(CONTAINS(?format, "/")) }
} LIMIT 40
"""

    # Query 2: creator links
    creators_sparql = SPARQL_PREFIXES + """
SELECT DISTINCT ?item ?author ?authorName WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:creator ?author .
  ?author foaf:name ?authorName .
} LIMIT 40
"""

    # Query 3: subject links
    subjects_sparql = SPARQL_PREFIXES + """
SELECT DISTINCT ?item ?subject WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:subject ?subject .
} LIMIT 60
"""

    # Query 4: series links
    series_sparql = SPARQL_PREFIXES + """
SELECT DISTINCT ?item ?series WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:isPartOf ?series .
} LIMIT 40
"""

    # Query 5: content type links
    types_sparql = SPARQL_PREFIXES + """
SELECT DISTINCT ?item ?type WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:type ?type .
  FILTER(STRSTARTS(STR(?type), "http://purl.org/dc/dcmitype/"))
} LIMIT 40
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
        except Exception:
            pass
        return []

    nodes = {}
    edges = []

    def add_node(nid, ntype, label, props=None):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "type": ntype, "label": label, "props": props or {}}

    # Works
    for row in sparql_query(works_sparql):
        nid   = row["item"]["value"]
        title = row.get("title", {}).get("value", nid.split("/")[-1])
        props = {}
        if "publisher" in row: props["publisher"] = row["publisher"]["value"]
        if "date"      in row: props["date"]      = row["date"]["value"][:10]
        if "format"    in row: props["format"]    = row["format"]["value"]
        add_node(nid, "work", title, props)

    # Persons + creator edges
    for row in sparql_query(creators_sparql):
        wid  = row["item"]["value"]
        pid  = row["author"]["value"]
        name = row.get("authorName", {}).get("value", pid.split("/")[-1])
        add_node(pid, "person", name, {"role": "Creator"})
        if wid in nodes:
            edges.append({"s": wid, "rel": "dcterms:creator", "t": pid})

    # Subjects (concepts) + subject edges
    subject_map = {}
    for row in sparql_query(subjects_sparql):
        wid     = row["item"]["value"]
        subject = row.get("subject", {}).get("value", "")
        if not subject:
            continue
        # deduplicate subjects by value
        sid = "concept_" + str(abs(hash(subject)) % 100000)
        if sid not in subject_map:
            subject_map[sid] = subject
            add_node(sid, "concept", subject[:40], {"scheme": "NDA Hungary"})
        if wid in nodes:
            edges.append({"s": wid, "rel": "dcterms:subject", "t": sid})

    # Series + isPartOf edges
    series_map = {}
    for row in sparql_query(series_sparql):
        wid    = row["item"]["value"]
        ser_id = row.get("series", {}).get("value", "")
        if not ser_id:
            continue
        short = ser_id.rstrip("/").split("/")[-1].replace("_", " ")
        if ser_id not in series_map:
            series_map[ser_id] = short
            add_node(ser_id, "series", short[:40], {"uri": ser_id})
        if wid in nodes:
            edges.append({"s": wid, "rel": "dcterms:isPartOf", "t": ser_id})

    # Content types + type edges
    type_labels = {
        "Sound": "dcmitype:Sound", "Text": "dcmitype:Text",
        "Image": "dcmitype:Image", "MovingImage": "dcmitype:MovingImage",
    }
    for row in sparql_query(types_sparql):
        wid      = row["item"]["value"]
        type_uri = row.get("type", {}).get("value", "")
        if not type_uri:
            continue
        short = type_uri.rstrip("/").split("/")[-1]
        label = type_labels.get(short, short)
        add_node(type_uri, "sound" if short == "Sound" else "other", label, {"uri": type_uri})
        if wid in nodes:
            edges.append({"s": wid, "rel": "dcterms:type", "t": type_uri})

    return jsonify({
        "nodes": list(nodes.values()),
        "edges": edges,
    })



@app.route("/sparql")
def sparql_explorer():
    return render_template('sparql.html')


if __name__ == "__main__":
    app.run(debug=True, port=5001)