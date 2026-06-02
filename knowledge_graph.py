
# ── SPARQL ────────────────────────────────────────────────────────────────────
SPARQL_PREFIXES = """PREFIX rdf:      <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs:     <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:      <http://www.w3.org/2002/07/owl#>
PREFIX dcterms:  <http://purl.org/dc/terms/>
PREFIX dcmitype: <http://purl.org/dc/dcmitype/>
PREFIX schema:   <http://schema.org/>
PREFIX foaf:     <http://xmlns.com/foaf/0.1/>
PREFIX skos:     <http://www.w3.org/2004/02/skos/core#>
PREFIX dbo:      <http://dbpedia.org/ontology/>
PREFIX lexvo:    <http://lexvo.org/id/iso639-3/>"""



CURATED_SCHEMA = """
DATASET SCHEMA (STRICT — USE ONLY THESE TERMS)

CLASSES ON WORKS:
- dbo:Work              (primary class — URI: http://dbpedia.org/ontology/Work)
- schema:CreativeWork   (always co-typed with dbo:Work)
- schema:Thing          (also co-typed, ignore for filtering)

CONTENT TYPE VALUES (objects of dcterms:type):
- dcmitype:Sound        (audio/radio content)
- dcmitype:Text         (books, articles)
- dcmitype:Image        (photographs)
- dcmitype:MovingImage  (video)
  dcterms:type may also carry a plain Hungarian string label e.g. "Magazin"

IMPORTANT — DUPLICATE TRIPLES:
The dataset contains every triple twice (confirmed from data). This is normal.
Use DISTINCT in SELECT to avoid duplicate rows in results.

PROPERTIES ON WORKS:
- rdfs:label            (display label — same as dcterms:title)
- dcterms:title         (title string)
- dcterms:creator       (URI → author node e.g. <http://lod.sztaki.hu/data/auth/356307>)
- dcterms:subject       (topic/keyword string, may be in Hungarian)
- dcterms:description   (free text, may be in Hungarian)
- dcterms:publisher     (publisher name string e.g. "RadioCafe")
- dcterms:date          (date string e.g. "2003-03-13 06:00:00+01" or just "2003")
- dcterms:type          (dcmitype URI and/or plain string label)
- dcterms:format        (MIME type e.g. "audio/mpeg" OR duration e.g. "32:16 (extent)")
- dcterms:identifier    (string starting with "URL: http://…")
- dcterms:language      (URI e.g. <http://lexvo.org/id/iso639-3/hun>)
- dcterms:isPartOf      (URI → parent series)

PROPERTIES ON AUTHORS:
- foaf:name             (full name e.g. "Pál Sümegi")
- rdfs:label            (same as foaf:name)
- dcterms:alternative   (Hungarian-order name e.g. "Sumegi Pal")
- owl:sameAs            (external authority URI)

RULES:
- ALWAYS use SELECT DISTINCT to avoid duplicate rows
- Filter works with: rdf:type dbo:Work
- Filter by content type: dcterms:type dcmitype:Sound / dcmitype:Text / dcmitype:Image
- Filter Hungarian: dcterms:language <http://lexvo.org/id/iso639-3/hun>
- Author name: JOIN dcterms:creator then foaf:name
- Text search: FILTER(regex(?var, "keyword", "i"))   ← NOT CONTAINS, NOT LCASE
- Year filter: FILTER(regex(?date, "^2003"))          ← NOT STRSTARTS, NOT CONTAINS
- Format filter: FILTER(regex(?format, "/"))          ← NOT CONTAINS
- Access URL: dcterms:identifier (value starts with "URL: ")
- NEVER use CONTAINS(), STRSTARTS(), LCASE(), BIND() — this Virtuoso does NOT support them
- NEVER filter authors by rdf:type
- NEVER use dc:title, dcterms:issued, skos:subject, virtrdf:*
"""


ENDPOINT_FACTS = """
CONFIRMED LIVE DATA FACTS (from endpoint inspection):

CLASS INSTANCE COUNTS:
- dbo:Work / schema:CreativeWork : ~1,153,997 instances  ← USE THIS for works
- foaf:Person / dbo:Person       : ~807,000 instances
- skos:Concept                   : ~743,000 instances
- dbo:Place                      : ~572,000 instances
- dbo:MusicalWork                : ~159,000 instances
- dbo:Film                       : ~71,000 instances
- dbo:Book                       : ~26,000 instances

PROPERTY USAGE COUNTS (higher = more reliably populated):
- dcterms:subject     : 13,990,778  ← very densely populated
- rdfs:label          :  5,988,878
- dcterms:creator     :  1,141,517
- dcterms:title       :    833,945
- dcterms:date        :    787,053
- dcterms:publisher   :    774,563
- dcterms:language    :    465,294
- dcterms:alternative :    346,418  ← author name in HU order
- dcterms:isPartOf    :    336,683
- dcterms:identifier  :    289,393
- dcterms:description :    284,405
- dcterms:type        :    275,385
- foaf:name           :    244,475
- dcterms:format      :    225,942
- dcterms:relation    :    204,824

ALSO AVAILABLE ON PERSONS (use for person queries):
- dbo:birthDate, dbo:deathDate
- dbo:birthPlace, dbo:deathPlace
- dbo:nationality
- dbo:abstract
- dbo:notableWork

REAL RECORD SHAPE (item/90 — confirmed working):
- rdf:type            → dbo:Work, schema:CreativeWork, schema:Thing
- rdfs:label          → "A helyes beszéd"
- dcterms:title       → "A helyes beszéd"
- dcterms:description → "Lin-csi apát elmélkedése…"
- dcterms:date        → "2003-03-13 06:00:00+01"   ← always full timestamp
- dcterms:identifier  → "URL: http://radio.sztaki.hu/…"  ← note the space after "URL:"
- dcterms:format      → "audio/mpeg" AND "32:16 (extent)"  ← two values per item
- dcterms:isPartOf    → <http://radio.sztaki.hu/node/showSeries.php/011se43>
- dcterms:language    → <http://lexvo.org/id/iso639-3/hun>
- dcterms:publisher   → "RadioCafe"
- dcterms:subject     → "Emberi tényezők, szociális ügyek"
- dcterms:type        → dcmitype:Sound AND "Magazin"  ← two values: URI + string label

IMPORTANT PATTERNS FROM REAL DATA:
- dcterms:date is always a full timestamp like "2003-03-13 06:00:00+01", never just "2003"
  → use FILTER(STRSTARTS(?date, "2003")) or BIND(SUBSTR(?date,1,4) AS ?year) for year queries
- dcterms:identifier always starts with "URL: " (with a space)
  → use FILTER(STRSTARTS(?identifier, "URL: ")) to get only access URLs
- dcterms:format has two values per item: MIME type and duration
  → use FILTER(CONTAINS(?format, "/")) to isolate MIME type
  → use FILTER(CONTAINS(?format, "extent")) to isolate duration
- dcterms:type has two values per item: a dcmitype: URI and a plain Hungarian label
  → use FILTER(STRSTARTS(STR(?type), "http://purl.org/dc/dcmitype/")) to get only URI types
- Every triple appears TWICE in this dataset → always use SELECT DISTINCT

CRITICAL — SPARQL VERSION RESTRICTION:
This Virtuoso endpoint runs SPARQL 1.0 only. The following SPARQL 1.1 functions are
NOT supported and will cause a 400 error:
- CONTAINS()   → use regex(?var, "text") instead
- STRSTARTS()  → use regex(?var, "^text") instead
- LCASE()      → use regex() with "i" flag for case-insensitive matching
- BIND()       → use the URI directly as the subject e.g. <http://...> dcterms:title ?title
- SUBSTR()     → not supported
- STR() inside FILTER is OK, regex(str(?var), "pattern") works

ALWAYS use:
- regex(?var, "keyword", "i")     for case-insensitive text search
- regex(?date, "^2003")           for year filtering
- regex(?format, "/")             for MIME type filtering
- regex(str(?type), "dcmitype")   for type URI filtering

"""


QUERY_EXAMPLES = """
EXAMPLE QUERIES (follow these patterns exactly):

# 1. List works with titles and content type
SELECT DISTINCT ?item ?title ?type WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:title ?title .
  OPTIONAL { ?item dcterms:type ?type . }
} LIMIT 15

# 2. Find works by a specific title keyword
SELECT DISTINCT ?item ?title WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:title ?title .
  FILTER(regex(?title, "mozart", "i"))
} LIMIT 15

# 3. Find works by subject keyword
SELECT DISTINCT ?item ?title ?subject WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:title ?title ;
        dcterms:subject ?subject .
  FILTER(regex(?subject, "history", "i"))
} LIMIT 15

# 4. Find audio/radio works with MIME format and identifier
SELECT DISTINCT ?item ?title ?format ?identifier WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:title ?title ;
        dcterms:type dcmitype:Sound .
  OPTIONAL { ?item dcterms:format ?format .
             FILTER(regex(?format, "/")) }
  OPTIONAL { ?item dcterms:identifier ?identifier . }
} LIMIT 15

# 5. Find works with their author name (JOIN on dcterms:creator)
SELECT DISTINCT ?title ?authorName WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:title ?title ;
        dcterms:creator ?author .
  ?author foaf:name ?authorName .
} LIMIT 15

# 6. Find works by author — search foaf:name and dcterms:alternative
SELECT DISTINCT ?title ?authorName ?altName WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:title ?title ;
        dcterms:creator ?author .
  ?author foaf:name ?authorName .
  OPTIONAL { ?author dcterms:alternative ?altName . }
  FILTER(regex(?authorName, "sumegi", "i") || regex(str(?altName), "sumegi", "i"))
} LIMIT 15

# 7. Find works by date/year with publisher
SELECT DISTINCT ?item ?title ?date ?publisher WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:title ?title ;
        dcterms:date ?date .
  OPTIONAL { ?item dcterms:publisher ?publisher . }
  FILTER(regex(?date, "^2003"))
} LIMIT 15

# 8. Find Hungarian language works with titles, creator names and series
SELECT DISTINCT ?item ?title ?authorName ?series WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:title ?title ;
        dcterms:language <http://lexvo.org/id/iso639-3/hun> .
  OPTIONAL { ?item dcterms:creator ?author .
             ?author foaf:name ?authorName . }
  OPTIONAL { ?item dcterms:isPartOf ?series . }
} LIMIT 15

# 9. Find works that are part of a series, with description
SELECT DISTINCT ?item ?title ?series ?description WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:title ?title ;
        dcterms:isPartOf ?series .
  OPTIONAL { ?item dcterms:description ?description . }
} LIMIT 15

# 10. Count works by dcmitype content type
SELECT DISTINCT ?type (COUNT(DISTINCT ?item) AS ?count) WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:type ?type .
  FILTER(regex(str(?type), "dcmitype"))
} GROUP BY ?type
ORDER BY DESC(?count)
LIMIT 15

# 11. Full Work entry — all known properties for a single item (NO BIND)
SELECT DISTINCT ?title ?authorName ?subject ?description ?publisher ?date ?type ?mimeFormat ?identifier ?language ?series WHERE {
  <http://lod.sztaki.hu/data/item/92> dcterms:title ?title .
  OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:creator ?author .
             OPTIONAL { ?author foaf:name ?authorName . } }
  OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:subject ?subject . }
  OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:description ?description . }
  OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:publisher ?publisher . }
  OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:date ?date . }
  OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:type ?type . }
  OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:format ?mimeFormat .
             FILTER(regex(?mimeFormat, "/")) }
  OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:identifier ?identifier . }
  OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:language ?language . }
  OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:isPartOf ?series . }
}

# 12. Count works by publisher — top publishers
SELECT DISTINCT ?publisher (COUNT(DISTINCT ?item) AS ?count) WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:publisher ?publisher .
} GROUP BY ?publisher
ORDER BY DESC(?count)
LIMIT 15

# 13. Count works by year
SELECT DISTINCT ?date (COUNT(DISTINCT ?item) AS ?count) WHERE {
  ?item rdf:type dbo:Work ;
        dcterms:date ?date .
} GROUP BY ?date
ORDER BY DESC(?count)
LIMIT 15
"""