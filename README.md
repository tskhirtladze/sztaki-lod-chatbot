# SZTAKI LOD Knowledge Graph Chat Assistant

A Flask + LangGraph application that lets users chat in plain English with the
[SZTAKI Linked Open Data](https://lod.sztaki.hu) knowledge graph of Hungarian
cultural heritage (books, movies, articles, authors, etc.). User questions are
classified, translated into SPARQL by a local LLM, executed against a Fuseki
SPARQL endpoint, optionally turned into a chart, and summarized back into
natural language - all streamed to the browser over Server-Sent Events.

## How it works

1. **classify** - decides whether the message needs data from the knowledge
   graph or is just casual conversation.
2. **kg_query** - asks the local LLM to generate a `SELECT DISTINCT … WHERE { … }`
   SPARQL query (SPARQL 1.0 only) and runs it against the Fuseki endpoint.
3. **visualize** - inspects the result rows and, if there's something worth
   plotting, builds a bar/pie chart config.
4. **summarize** - asks the LLM to turn the raw results into a short,
   non-technical answer.
5. **casual_chat** - handles greetings / off-topic messages directly.

Conversation state is persisted per browser session using a LangGraph
`SqliteSaver` checkpointer (`memory.db`), so the assistant remembers prior
turns in a thread.

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally, with the `llama3.1` model
  pulled (this project uses **no cloud LLM** - everything runs on your
  machine)
- Network access to a SPARQL endpoint (defaults to the public
  `https://lod.sztaki.hu/sparql` endpoint)

## 1. Install and run a local LLM with Ollama

This app talks to a local model through `langchain_ollama.ChatOllama`, so you
need Ollama installed and serving the `llama3.1` model before starting the
Flask app.

### Install Ollama

- **macOS / Windows**: download the installer from
  [ollama.com/download](https://ollama.com/download) and run it.
- **Linux**:
  ```bash
  curl -fsSL https://ollama.com/install.sh | sh
  ```

### Pull the model

```bash
ollama pull llama3.1
```

This downloads the 8B-parameter Llama 3.1 model.

### Start the Ollama server

Ollama usually starts automatically as a background service after
installation. If it isn't running, start it manually:

```bash
ollama serve
```

By default it listens on `http://localhost:11434`, which is what
`ChatOllama` connects to out of the box. Verify it's working:

```bash
ollama run llama3.1 "say hi"
```

If you want to use a different model, change the `model="llama3.1"` argument
in `app.py` (in the `ChatOllama(...)` initialization) to whatever you've
pulled, e.g. `ollama pull mistral` and `model="mistral"`.

## 2. Set up the Python project

Clone the repo, then create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## 3. Configuration

All configuration is via environment variables (optional - sensible defaults
are built in):

| Variable           | Default                        | Description                                  |
|---------------------|---------------------------------|-----------------------------------------------|
| `FUSEKI_ENDPOINT`   | `https://lod.sztaki.hu/sparql` | SPARQL endpoint to query                      |
| `FUSEKI_TIMEOUT`    | `75`                            | Request timeout in seconds                    |
| `LLM_MAX_TOKENS`    | `1200`                         | Max tokens generated per LLM call             |
| `FLASK_SECRET_KEY`  | random                         | Flask session secret                          |
| `MEMORY_DB`         | `memory.db`                   | SQLite file for LangGraph checkpoints         |

## 4. Run the app

Make sure Ollama is running (`ollama serve`) and the `llama3.1` model has
been pulled, then:

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
- `/graph/data` – JSON endpoint with sample nodes/edges from the knowledge graph
- `/sparql` – simple SPARQL explorer page

## Project structure

```
.
├── app.py               # Flask app, LangGraph pipeline, SPARQL helpers
├── knowledge_graph.py   # SPARQL prefixes, schema notes, example queries
├── templates/
│   ├── index.html       # chat UI
│   ├── graph.html       # graph visualization
│   └── sparql.html      # SPARQL explorer
└── memory.db            # SQLite-backed conversation checkpoints (auto-created)
```
