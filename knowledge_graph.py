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
PREFIX dbp:      <http://dbpedia.org/property/>
PREFIX lexvo:    <http://lexvo.org/id/iso639-3/>"""

# This endpoint hosts many unrelated named graphs (tagging data, geonames,
# openlinksw cartridge ontologies, an "evri" general-knowledge dump, etc) that
# happen to reuse the same property names (foaf:name, dcterms:date, ...) as
# this dataset. Confirmed by direct inspection (GRAPH ?g { ?s ?p ?o } COUNT):
# the actual SZTAKI cultural-heritage works/authors live ONLY in this graph.
NDA_GRAPH = "http://lod.sztaki.hu/nda"



CURATED_SCHEMA = """
DATASET SCHEMA (STRICT — USE ONLY THESE TERMS)

CRITICAL — GRAPH SCOPING:
This endpoint hosts many unrelated named graphs sharing the same property
names as this dataset (tagging data, geonames, an unrelated "evri" dump of
countries/politicians/companies, etc). EVERY query MUST be scoped inside
GRAPH <http://lod.sztaki.hu/nda> { ... } or it will silently mix in data
from other, unrelated graphs. Never omit this.

CLASSES ON WORKS:
- dbo:Work              (primary class — URI: http://dbpedia.org/ontology/Work)
- schema:CreativeWork   (always co-typed with dbo:Work)
- schema:Thing          (also co-typed, ignore for filtering)

CLASSES ON PEOPLE:
- foaf:Person / dbo:Person / schema:Person   (all three co-typed on the same node)

GRAPH SHAPE — how entities actually connect (read this before writing any JOIN):

    [Work]──dcterms:creator──▶[Person]
      │                          │
      │                    foaf:name, dcterms:alternative,
      │                    dbp:birthYear, dbp:deathYear
      │
      ├──dcterms:subject────▶ "plain string" (NOT a URI, NOT a linked Concept node —
      │                        it's just a literal value sitting on the Work)
      │
      ├──dcterms:isPartOf───▶[Series URI] (a bare URI, no properties of its own —
      │                        treat it as an opaque grouping id, not a queryable node)
      │
      ├──dcterms:type────────▶ dcmitype:Sound / Text / Image / MovingImage (URI)
      │                         AND a plain-string label e.g. "Magazin" (both present)
      │
      └──dcterms:title, rdfs:label, dcterms:date, dcterms:publisher,
         dcterms:description, dcterms:format, dcterms:identifier,
         dcterms:language  ──▶ plain string / literal values on the Work itself

    [Person]──owl:sameAs──▶[external authority URI, e.g. VIAF]  (dead end — do not
                             expect further properties past this link)

THE ONE HOP THAT MATTERS: Work → dcterms:creator → Person → foaf:name.
That is the only real join in this dataset. Everything else (subject, series,
type, format, identifier, language) is a flat literal or opaque URI sitting
directly on the Work — there is nothing more to "join into" beyond it, so
never write a second OPTIONAL hop off of ?subject, ?series, or ?type expecting
more properties; there are none.

IMPORTANT — CREATOR NODES AREN'T ALWAYS TYPED foaf:Person AT ALL: confirmed by
directly cross-referencing ?work a ?workType ; dcterms:creator ?creator .
?creator a ?creatorType — the creator's type is sometimes ONLY schema:Thing,
with NO foaf:Person / dbo:Person / schema:Person co-typing whatsoever (as well
as, separately, cases where it IS co-typed as foaf:Person but represents an
organization or office rather than an individual human, e.g. a statistical
office or publishing house). Two distinct traps, same fix:
  1. Don't assume rdf:type foaf:Person on a creator node means an individual
     human — it just means "the node dcterms:creator points to."
  2. Don't assume a creator node is typed foaf:Person at all — some are only
     schema:Thing.
NEVER add rdf:type foaf:Person (or any type filter) on the ?author/?creator
variable when joining for a name — just OPTIONAL-fetch foaf:name directly off
whatever dcterms:creator points to, with no type constraint. This is also the
likely source of at least part of the schema:Thing vs Work+Person count gap
noted below: some creator-only nodes are typed schema:Thing and nothing else.

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
- dcterms:identifier    (string starting with "URL: http://…" — but see caveat below)
- dcterms:language      (URI e.g. <http://lexvo.org/id/iso639-3/hun>)
- dcterms:isPartOf      (URI → parent series)
- dcterms:relation      (URI, 200k+ triples — opaque related-item link, same treatment
                          as isPartOf: nothing further to join into off of it)

CAVEAT — MIXED LITERAL/IRI TYPING ON A FEW PROPERTIES:
Confirmed by direct isLiteral()/isIRI() inspection of the nda graph: dcterms:identifier,
dcterms:title, and dcterms:relation each appear as a LITERAL on some triples and an IRI
on others. Do NOT assume identifier is always a "URL: " string, or that title is always
plain text — OPTIONAL-fetch these with no type assumption and let application code
handle either shape; never FILTER(isLiteral(...)) or FILTER(isIRI(...)) on them, since
that would silently drop real rows.

PROPERTIES ON AUTHORS/PEOPLE:
- foaf:name             (full name e.g. "Pál Sümegi")
- rdfs:label            (same as foaf:name)
- dcterms:alternative   (Hungarian-order name e.g. "Sumegi Pal")
- owl:sameAs            (external authority URI)
- dbp:birthYear         (year only, e.g. "1938" — NOT dbo:birthDate, that
                          property does not exist in this dataset)
- dbp:deathYear         (year only, e.g. "2011" — NOT dbo:deathDate)

RULES:
- ALWAYS scope every query inside GRAPH <http://lod.sztaki.hu/nda> { ... }
- ALWAYS use SELECT DISTINCT to avoid duplicate rows
- Filter works with: rdf:type dbo:Work
- Filter by content type: dcterms:type dcmitype:Sound / dcmitype:Text / dcmitype:Image
  (use the URI directly as the object — no regex needed, it's an exact match)
- Filter Hungarian: dcterms:language <http://lexvo.org/id/iso639-3/hun>
  (exact match — no regex needed)
- Author name: JOIN dcterms:creator then foaf:name
- Access URL: dcterms:identifier — usually a "URL: " string, but occasionally an IRI;
  fetch unfiltered and let application code handle either shape
- NEVER use CONTAINS(), STRSTARTS(), LCASE(), BIND() — this Virtuoso does NOT support them
- NEVER filter authors by rdf:type (some creator nodes are typed only
  schema:Thing, not foaf:Person — a type filter would silently drop them)
- NEVER use dc:title, dcterms:issued, skos:subject, virtrdf:*
- NEVER use dbo:birthDate, dbo:deathDate, dbo:birthPlace, dbo:deathPlace,
  dbo:nationality, dbo:abstract, dbo:notableWork — none of these exist on
  people in this dataset (only dbp:birthYear / dbp:deathYear do)

PREFER DIRECT / EXACT MATCHING — AVOID regex() WHEN POSSIBLE:
- Do NOT add a FILTER just to split apart multi-valued properties (e.g. dcterms:format
  having both a MIME type and a duration, or dcterms:type having both a URI and a plain
  label). Instead, just OPTIONAL-fetch the property with no FILTER and let the
  application code pick apart the values it needs.
- If you need to match one of a small, known set of exact values (e.g. dcmitype:Sound /
  dcmitype:Text / dcmitype:Image / dcmitype:MovingImage), write it as an exact triple
  pattern or an equality comparison (?type = dcmitype:Sound), never as regex.
- Only fall back to regex() when there is truly no exact-match alternative — i.e. genuine
  free-text keyword search where the user supplied an arbitrary search term (searching a
  title, subject, description, or author name for a substring, case-insensitively).
  In that fallback case only: FILTER(regex(?var, "keyword", "i"))
"""


ENDPOINT_FACTS = """
CONFIRMED LIVE DATA FACTS — scoped to GRAPH <http://lod.sztaki.hu/nda> ONLY
(earlier figures mixed in unrelated graphs and were wrong; these are re-verified):

CLASS INSTANCE COUNTS (inside GRAPH <http://lod.sztaki.hu/nda>):
- dbo:Work / schema:CreativeWork        : 807,878 instances  ← USE THIS for works
- foaf:Person / dbo:Person / schema:Person : 37,777 instances
- schema:Thing                           : 1,043,166 instances  ← does NOT equal
  Work+Person (845,655). The ~197,500 gap is PARTIALLY EXPLAINED: cross-referencing
  ?work a ?workType ; dcterms:creator ?creator . ?creator a ?creatorType confirms
  some dcterms:creator targets are typed ONLY schema:Thing, with no foaf:Person /
  dbo:Person / schema:Person co-typing at all — these creator-only nodes inflate
  schema:Thing's count without appearing in the Work or Person totals. The exact
  size of that contribution is still not counted, so schema:Thing is still not a
  safe substitute filter for dbo:Work (or for "is a Person") in generated queries.

PROPERTY USAGE COUNTS (triple count for the predicate, inside nda only):
- rdf:type            : 2,772,253
- dcterms:creator      : 1,063,875
- rdfs:label           : 1,055,711
- dcterms:type         :   896,523
- dcterms:title        :   820,423
- dcterms:date         :   773,150
- dcterms:publisher    :   766,068
- dcterms:language     :   452,872
- dcterms:subject      :   375,083
- dcterms:alternative  :   341,800  ← author name in HU order
- dcterms:isPartOf     :   328,534
- dcterms:identifier   :   283,553
- dcterms:description  :   277,791
- foaf:name            :   235,288
- dcterms:format       :   220,267
- dcterms:relation     :   200,728
- owl:sameAs           :   182,865
- dbp:birthYear        :    41,510  ← on people; year only, not a full date
- dbp:deathYear        :    11,700  ← on people; year only, not a full date

ALSO AVAILABLE ON PERSONS (use for person queries) — CONFIRMED, nothing else:
- dbp:birthYear, dbp:deathYear (note the dbp: prefix, NOT dbo:)
No other person properties (birthDate, birthPlace, nationality, abstract,
notableWork, etc) exist in this graph — do not use them even if they seem
like standard DBpedia properties elsewhere.

REAL RECORD SHAPE (item/90 — confirmed working, inside GRAPH nda):
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
  → don't filter this in SPARQL at all: OPTIONAL-fetch ?date with no FILTER, and let the
    application code compare the first 4 characters against the year it wants
- dcterms:identifier always starts with "URL: " (with a space)
  → don't filter this in SPARQL: OPTIONAL-fetch ?identifier with no FILTER, and let the
    application code strip the "URL: " prefix
- dcterms:format has two values per item: MIME type and duration
  → don't filter this in SPARQL: OPTIONAL-fetch ?format with no FILTER, and let the
    application code separate the value containing "/" (MIME type) from the one
    containing "extent" (duration)
- dcterms:type has two values per item: a dcmitype: URI and a plain Hungarian label
  → to select ONLY the URI type, use an exact equality comparison against the known
    set, e.g. FILTER(?type = dcmitype:Sound || ?type = dcmitype:Text ||
    ?type = dcmitype:Image || ?type = dcmitype:MovingImage) — never regex
- Every triple appears TWICE in this dataset → always use SELECT DISTINCT
- This endpoint hosts multiple unrelated named graphs sharing property names
  with this dataset → ALWAYS wrap the query body in GRAPH <http://lod.sztaki.hu/nda> { ... }

CRITICAL — SPARQL VERSION RESTRICTION:
This Virtuoso endpoint runs SPARQL 1.0 only. The following SPARQL 1.1 functions are
NOT supported and will cause a 400 error:
- CONTAINS()   → not supported, no direct replacement — restructure the query to avoid
                 needing substring matching (fetch unfiltered, filter in app code), or as
                 a last resort use regex(?var, "text")
- STRSTARTS()  → not supported — same as above; last resort: regex(?var, "^text")
- LCASE()      → not supported — case-insensitive matching has no exact-match
                 alternative; last resort: regex() with the "i" flag
- BIND()       → use the URI directly as the subject e.g. <http://...> dcterms:title ?title
- SUBSTR()     → not supported
- STR() inside FILTER is OK, e.g. FILTER(?type = dcmitype:Sound) works without STR()

PREFERRED APPROACH — avoid regex() wherever an exact match or app-side filtering works:
- Known small value sets (content type, language) → exact triple pattern / equality
- Multi-valued properties that need splitting (format, type, identifier) → fetch
  unfiltered with OPTIONAL, split in application code
- Year filtering → fetch ?date unfiltered, compare prefix in application code

regex() AS FALLBACK — use ONLY when there is no exact-match alternative, i.e. genuine
free-text keyword search on user-supplied terms (title / subject / description / author
name containing an arbitrary word, case-insensitively):
- regex(?var, "keyword", "i")     for case-insensitive free-text search

"""


QUERY_EXAMPLES = """
EXAMPLE QUERIES (follow these patterns exactly — note every WHERE body is
wrapped in GRAPH <http://lod.sztaki.hu/nda> { ... }, which is REQUIRED):

# 1. List works with titles and content type
SELECT DISTINCT ?item ?title ?type WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?item rdf:type dbo:Work ;
          dcterms:title ?title .
    OPTIONAL { ?item dcterms:type ?type . }
  }
} LIMIT 15

# 2. Find works by a specific title keyword
# (regex fallback — genuine free-text search on a user-supplied keyword, no exact-match alternative)
SELECT DISTINCT ?item ?title WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?item rdf:type dbo:Work ;
          dcterms:title ?title .
    FILTER(regex(?title, "mozart", "i"))
  }
} LIMIT 15

# 3. Find works by subject keyword
# (regex fallback — genuine free-text search)
SELECT DISTINCT ?item ?title ?subject WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?item rdf:type dbo:Work ;
          dcterms:title ?title ;
          dcterms:subject ?subject .
    FILTER(regex(?subject, "history", "i"))
  }
} LIMIT 15

# 4. Find audio/radio works with format and identifier
# (fetch dcterms:format / dcterms:identifier unfiltered — no regex needed;
#  application code picks out the MIME-type value and strips the "URL: " prefix)
SELECT DISTINCT ?item ?title ?format ?identifier WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?item rdf:type dbo:Work ;
          dcterms:title ?title ;
          dcterms:type dcmitype:Sound .
    OPTIONAL { ?item dcterms:format ?format . }
    OPTIONAL { ?item dcterms:identifier ?identifier . }
  }
} LIMIT 15

# 5. Find works with their author name (JOIN on dcterms:creator)
SELECT DISTINCT ?title ?authorName WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?item rdf:type dbo:Work ;
          dcterms:title ?title ;
          dcterms:creator ?author .
    ?author foaf:name ?authorName .
  }
} LIMIT 15

# 6. Find works by author — search foaf:name and dcterms:alternative
# (regex fallback — genuine free-text search on a user-supplied name, no exact-match alternative)
SELECT DISTINCT ?title ?authorName ?altName WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?item rdf:type dbo:Work ;
          dcterms:title ?title ;
          dcterms:creator ?author .
    ?author foaf:name ?authorName .
    OPTIONAL { ?author dcterms:alternative ?altName . }
    FILTER(regex(?authorName, "sumegi", "i") || regex(str(?altName), "sumegi", "i"))
  }
} LIMIT 15

# 7. Find works by date/year with publisher
# (fetch dcterms:date unfiltered — no regex needed; application code compares
#  the first 4 characters of ?date against the year requested)
SELECT DISTINCT ?item ?title ?date ?publisher WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?item rdf:type dbo:Work ;
          dcterms:title ?title ;
          dcterms:date ?date .
    OPTIONAL { ?item dcterms:publisher ?publisher . }
  }
} LIMIT 15

# 8. Find Hungarian language works with titles, creator names and series
SELECT DISTINCT ?item ?title ?authorName ?series WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?item rdf:type dbo:Work ;
          dcterms:title ?title ;
          dcterms:language <http://lexvo.org/id/iso639-3/hun> .
    OPTIONAL { ?item dcterms:creator ?author .
               ?author foaf:name ?authorName . }
    OPTIONAL { ?item dcterms:isPartOf ?series . }
  }
} LIMIT 15

# 9. Find works that are part of a series, with description
SELECT DISTINCT ?item ?title ?series ?description WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?item rdf:type dbo:Work ;
          dcterms:title ?title ;
          dcterms:isPartOf ?series .
    OPTIONAL { ?item dcterms:description ?description . }
  }
} LIMIT 15

# 10. Count works by dcmitype content type
# (exact equality against the known set of 4 dcmitype URIs — no regex needed)
SELECT DISTINCT ?type (COUNT(DISTINCT ?item) AS ?count) WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?item rdf:type dbo:Work ;
          dcterms:type ?type .
    FILTER(?type = dcmitype:Sound || ?type = dcmitype:Text ||
           ?type = dcmitype:Image || ?type = dcmitype:MovingImage)
  }
} GROUP BY ?type
ORDER BY DESC(?count)
LIMIT 15

# 11. Full Work entry — all known properties for a single item (NO BIND)
SELECT DISTINCT ?title ?authorName ?subject ?description ?publisher ?date ?type ?mimeFormat ?identifier ?language ?series WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    <http://lod.sztaki.hu/data/item/92> dcterms:title ?title .
    OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:creator ?author .
               OPTIONAL { ?author foaf:name ?authorName . } }
    OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:subject ?subject . }
    OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:description ?description . }
    OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:publisher ?publisher . }
    OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:date ?date . }
    OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:type ?type . }
    OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:format ?mimeFormat . }
    OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:identifier ?identifier . }
    OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:language ?language . }
    OPTIONAL { <http://lod.sztaki.hu/data/item/92> dcterms:isPartOf ?series . }
  }
}

# 12. Count works by publisher — top publishers
SELECT DISTINCT ?publisher (COUNT(DISTINCT ?item) AS ?count) WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?item rdf:type dbo:Work ;
          dcterms:publisher ?publisher .
  }
} GROUP BY ?publisher
ORDER BY DESC(?count)
LIMIT 15

# 13. Count works by year
SELECT DISTINCT ?date (COUNT(DISTINCT ?item) AS ?count) WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?item rdf:type dbo:Work ;
          dcterms:date ?date .
  }
} GROUP BY ?date
ORDER BY DESC(?count)
LIMIT 15

# 14. Find authors with birth/death year (person query — dbp:, NOT dbo:)
SELECT DISTINCT ?authorName ?birthYear ?deathYear WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?author rdf:type foaf:Person ;
            foaf:name ?authorName .
    OPTIONAL { ?author dbp:birthYear ?birthYear . }
    OPTIONAL { ?author dbp:deathYear ?deathYear . }
  }
} LIMIT 15

# 20. dcterms:relation, like isPartOf, is an opaque URI link with nothing further
# to join into — fetch it unfiltered, never assume it's always a literal.
SELECT DISTINCT ?workTitle ?relation WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?work rdf:type dbo:Work ;
          dcterms:title ?workTitle .
    OPTIONAL { ?work dcterms:relation ?relation . }
  }
} LIMIT 15

# ── The following examples exist to teach the GRAPH SHAPE, not just syntax ──

# 15. THE ONE REAL JOIN in this dataset: Work → creator → Person → name.
# Every other property (subject, series, format...) is a flat literal or a
# dead-end URI sitting directly on the Work — nothing to join into further.
# This is the only pattern with two hops; treat every other property as one hop.
SELECT DISTINCT ?workTitle ?authorName WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?work rdf:type dbo:Work ;
          dcterms:title ?workTitle ;
          dcterms:creator ?author .
    ?author foaf:name ?authorName .
  }
} LIMIT 15

# 16. "Named entity" search that could be EITHER a work title OR a creator —
# don't assume which one the user means. A person, office, or organization
# name (e.g. a statistical office, a publishing house) is stored as a
# foaf:Person node reachable via dcterms:creator, not as a Work — searching
# only dcterms:title for an organization's name will find nothing, because
# organizations never have their own Work-typed entry, only a creator entry.
# Two UNION branches, no BIND (still SPARQL 1.0 only) — the two result rows
# tell you which branch matched by which columns are populated.
SELECT DISTINCT ?workTitle ?authorName WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    {
      ?work rdf:type dbo:Work ; dcterms:title ?workTitle .
      FILTER(regex(?workTitle, "statisztikai", "i"))
    } UNION {
      ?work rdf:type dbo:Work ; dcterms:title ?workTitle ; dcterms:creator ?author .
      ?author foaf:name ?authorName .
      FILTER(regex(?authorName, "statisztikai", "i"))
    }
  }
} LIMIT 15

# 17. Find OTHER works by the same creator as a given work — a real 3-hop
# traversal (Work A → creator → Person → creator-of → Work B). This is the
# only place a "co-occurrence" style query makes sense in this schema, since
# Person is the only node type that multiple Works actually point back to.
SELECT DISTINCT ?otherTitle WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?knownWork dcterms:title ?knownTitle ;
               dcterms:creator ?author .
    FILTER(regex(?knownTitle, "A helyes beszéd", "i"))
    ?otherWork dcterms:creator ?author ;
               dcterms:title ?otherTitle .
    FILTER(?otherWork != ?knownWork)
  }
} LIMIT 15

# 18. dcterms:subject is a flat string, NOT a linked concept — do not try to
# join through it or treat it as a URI. This is the wrong pattern:
#   ?work dcterms:subject ?subj . ?subj skos:prefLabel ?label .   ← WRONG, ?subj has no further properties
# This is the right pattern — it's just a value to read or filter directly:
SELECT DISTINCT ?workTitle ?subject WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?work rdf:type dbo:Work ;
          dcterms:title ?workTitle ;
          dcterms:subject ?subject .
  }
} LIMIT 15

# 19. dcterms:isPartOf (series) is an opaque URI with no queryable properties
# of its own in this dataset — use it only to GROUP works together, never to
# look up a series "title" or "description" (there isn't one).
SELECT DISTINCT ?series (COUNT(DISTINCT ?work) AS ?worksInSeries) WHERE {
  GRAPH <http://lod.sztaki.hu/nda> {
    ?work rdf:type dbo:Work ;
          dcterms:isPartOf ?series .
  }
} GROUP BY ?series
ORDER BY DESC(?worksInSeries)
LIMIT 15
"""