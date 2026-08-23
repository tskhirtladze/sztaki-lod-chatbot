# SZTAKI LOD Knowledge Graph Chat Assistant

A Flask + LangGraph application that lets users chat in plain English with the
[SZTAKI Linked Open Data](https://lod.sztaki.hu) knowledge graph of Hungarian
cultural heritage (books, movies, articles, authors, etc.). User questions are
checked against the graph's schema, translated into SPARQL by Claude, executed
against a Fuseki SPARQL endpoint, optionally turned into a chart, and
summarized back into natural language - all streamed to the browser over
Server-Sent Events.

## How it works

Every message goes straight through the knowledge-graph pipeline (there's no
separate intent classifier or casual-chat shortcut - the summarizer handles
greetings/off-topic messages by explaining what it can look up instead):

1. **kg_query** - a single node that:
   - checks whether the question maps to concepts that actually exist in the
     graph's schema, before spending a call generating SPARQL for it
   - runs an entity-resolution pass so named entities (people, publishers,
     titles) are matched against how they're actually stored in the graph,
     rather than trusting the user's original wording/language
   - asks Claude to generate a `SELECT DISTINCT … WHERE { … }` SPARQL query
     (SPARQL 1.0 only) and runs it against the Fuseki endpoint
   - retries once with a broadened query if the first attempt comes back empty
2. **visualize** - inspects the result rows and, if there's something worth
   plotting, builds a bar/pie/line chart with matplotlib.
3. **summarize** - asks Claude to turn the raw results (or the reason nothing
   was found) into a short, non-technical answer.

Conversation state is persisted per browser session using a LangGraph
`SqliteSaver` checkpointer (`memory.db`), so the assistant remembers prior
turns in a thread.

## Requirements

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/) - this app calls the
  Claude API (`claude-opus-4-8` by default) via `langchain_anthropic`, so
  there's no local LLM to install or run
- Network access to a SPARQL endpoint (defaults to the public
  `https://lod.sztaki.hu/sparql` endpoint)

## 1. Set up the Python project

Clone the repo, then create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## 2. Configure your API key

Set your Anthropic API key as an environment variable (or put it in a `.env`
file in the project root - it's loaded automatically via `python-dotenv`):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## 3. Configuration

Further configuration is via environment variables (optional - sensible
defaults are built in):

| Variable            | Default                        | Description                                    |
|----------------------|---------------------------------|--------------------------------------------------|
| `ANTHROPIC_API_KEY`  | *(required)*                   | API key used by `langchain_anthropic`             |
| `FUSEKI_ENDPOINT`    | `https://lod.sztaki.hu/sparql` | SPARQL endpoint to query                          |
| `FUSEKI_TIMEOUT`     | `75`                            | Request timeout in seconds                        |
| `LLM_MAX_TOKENS`     | `1200`                         | Max tokens generated per LLM call                 |
| `FLASK_SECRET_KEY`   | auto-generated & persisted     | Flask session secret                              |
| `MEMORY_DB`          | `memory.db`                   | SQLite file for LangGraph checkpoints             |

If `FLASK_SECRET_KEY` isn't set, a random key is generated once and saved to
a local file (`.flask_secret_key` by default, override with
`SECRET_KEY_FILE`) so it stays stable across restarts and multiple workers.
Set `FLASK_SECRET_KEY` explicitly for real multi-host deployments.

## 4. Run the app

```bash
python app.py
```

The app starts in debug mode on **http://localhost:5001**.

Available pages/routes:

- `/` – main chat UI
- `/chat/stream` – POST endpoint, streams the assistant's reply via SSE
- `/chat` – POST endpoint, non-streaming fallback
- `/reset` – POST endpoint, starts a fresh conversation thread
- `/graph` – graph visualization page
- `/graph/data` – JSON endpoint with a bounded sample of nodes/edges from the
  knowledge graph (capped at 10,000 triples - not the full graph)
- `/about` – about page with endpoint/graph info and dataset size

## Project structure

```
.
├── app.py               # Flask app, LangGraph pipeline, SPARQL helpers
├── knowledge_graph.py   # SPARQL prefixes, schema notes, example queries
├── templates/
│   ├── index.html       # chat UI
│   ├── graph.html       # graph visualization
│   └── about.html       # about page
└── memory.db             # SQLite-backed conversation checkpoints (auto-created)
```