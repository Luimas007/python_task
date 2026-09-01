# Testing the API with Postman

Eight steps to run any endpoint. The worked example is `POST /api/ask`, the one
worth demonstrating.

**Before you start:** the assistant must be running.

```bash
python app.py
```

Leave it running in its own terminal. Check it is up by opening
<http://127.0.0.1:8000/api/health> in a browser — you should see JSON.

---

## The fast route: import the collection

Skip the manual steps entirely.

1. In Postman click **Import** (top left).
2. Drop in [`postman_collection.json`](postman_collection.json) — or download it
   from the running app at
   <http://127.0.0.1:8000/docs/postman_collection.json>.
3. A **Samsung Phone Assistant** collection appears with every endpoint
   pre-filled, grouped into *Ask*, *Knowledge base*, *Catalogue* and *System*.
4. Open any request and hit **Send**.

The collection uses a `base_url` variable set to `http://127.0.0.1:8000`. If you
serve on another port, change it once under the collection's **Variables** tab.

---

## The manual route: eight steps

### 1. Open Postman

Launch the desktop app, or use <https://web.postman.co>. You can skip signing
in — click **Skip and go to the app**.

> The web version cannot reach `127.0.0.1` unless you install the Postman Desktop
> Agent. The desktop app is simpler for a local API.

### 2. Create a request

Click **New → HTTP**, or the **+** above the tab bar. An untitled request opens
with a method dropdown and a URL field.

### 3. Select the HTTP method

Open the dropdown to the left of the URL bar — it says `GET` — and choose
**POST**.

Use `GET` for every read endpoint (`/api/health`, `/api/phones`,
`/api/rankings`, …); those need no body at all, so you can skip steps 5 and 6.

### 4. Enter the URL

```
http://127.0.0.1:8000/api/ask
```

Use `127.0.0.1` rather than `localhost`. On some Windows setups `localhost`
resolves to IPv6 first and the connection is refused.

### 5. Add the headers

Open the **Headers** tab:

| Key | Value |
|---|---|
| `Content-Type` | `application/json` |

Postman usually adds this automatically once you pick a JSON body in the next
step — just confirm it is there. **No authentication header is needed**; the API
has no auth.

### 6. Add the request body

Open the **Body** tab → select **raw** → set the format dropdown on the right
to **JSON**. Paste:

```json
{
  "question": "How does the Galaxy S25 Ultra compare to the S24 Ultra?",
  "session_key": "postman-demo"
}
```

### 7. Send

Click **Send**.

The first question after startup takes a few seconds while the language model
loads. After that: about a second for lookups and rankings, five to ten seconds
for comparisons and reviews.

### 8. Read the response

The bottom pane shows the JSON body, the status code and the elapsed time.

| Field | What it tells you |
|---|---|
| `answer` | The prose reply |
| `agents_used` | Which agents ran, in order — the multi-agent flow |
| `pipeline` | The route the intent took |
| `devices` | Phones resolved against the database |
| `grounding.verdict` | `grounded` = every number matched a database row |
| `unresolved` | Devices mentioned that are **not** in the database |
| `trace` | Expand it: every agent message and the actual SQL each one ran |

`trace` is the interesting one for a demo. Expand it and you can point at real
`SELECT` statements with row counts and millisecond timings — that is the proof
the answer came from PostgreSQL and not from the model's memory.

---

## Requests worth demonstrating

### Multi-agent comparison

`POST http://127.0.0.1:8000/api/ask`

```json
{ "question": "How does the Galaxy S25 Ultra compare to the S24 Ultra?" }
```

Watch `agents_used` → `["NEXUS","ATLAS","SPECTRA","VERSUS","SENTINEL"]`, and
`extras.deltas` for every numeric difference computed in Python rather than by
the model.

### A ranking, with honest NULLs

```json
{ "question": "Which Samsung phone has the best battery life?" }
```

`extras.ranking.excluded_null_count` reports how many phones were left out
because the source never published the value.

### A phone that is not in the database

```json
{ "question": "What is the battery capacity of the Galaxy S99 Omega?" }
```

`unresolved` names the device and the answer says it is not in the database.
Nothing is invented. This is the no-external-knowledge rule, demonstrable in one
request.

### Refresh the knowledge base

`POST http://127.0.0.1:8000/api/knowledge/refresh`

```json
{ "limit": 10 }
```

Returns immediately. Watch it land with
`GET http://127.0.0.1:8000/api/knowledge/refresh/status`, or in the console's
Knowledge base panel.

### The audit trail

`GET http://127.0.0.1:8000/api/query-log?limit=20`

Every SQL statement the agents ran, with the issuing agent, row count and
duration.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Error: connect ECONNREFUSED` | The app is not running | Start it: `python app.py` |
| Works in the browser, not in Postman web | Web Postman cannot reach localhost | Use the desktop app, or install the Postman Desktop Agent |
| `422 Unprocessable Entity` | Body is not valid JSON, or `question` is missing | Check **raw + JSON** is selected, and that the JSON parses |
| `409 Conflict` on refresh | A refresh is already running | Wait for it, or poll `/api/knowledge/refresh/status` |
| Answer says the phone is not in the database | It genuinely is not loaded | `GET /api/phones` to see what is; refresh with a bigger `limit` |
| Very slow first request | The model is loading into VRAM | Normal. Subsequent requests are fast |
