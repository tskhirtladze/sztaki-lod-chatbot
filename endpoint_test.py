import urllib.request
import urllib.parse
import json
import ssl

# Fix for macOS Python SSL certificate issue
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

endpoint = "https://lod.sztaki.hu/sparql"
query = """
SELECT DISTINCT ?p WHERE {
  ?s ?p ?o .
}
ORDER BY ?p
LIMIT 5
"""

data = urllib.parse.urlencode({
    "query": query,
    "format": "json"
}).encode("utf-8")

req = urllib.request.Request(
    endpoint,
    data=data,
    headers={
        "Accept": "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (compatible; SPARQLClient/1.0)"
    },
    method="POST"
)

with urllib.request.urlopen(req, timeout=25, context=ctx) as response:
    result = json.loads(response.read().decode())

print(f"Columns : {result['head']['vars']}")
print(f"Rows    : {len(result['results']['bindings'])}")
print()
for row in result["results"]["bindings"]:
    uri      = row.get("URI",        {}).get("value", "N/A")
    obj_type = row.get("ObjectType", {}).get("value", "N/A")
    print(f"{uri}\n  -> {obj_type}\n")