---
title: "Meridian Home"
type: index
created: "2026-04-04"
updated: "2026-04-04"
---

# Meridian

Personal knowledge system — LLM-maintained wiki built from captured sources.

[[_index|Full Index]] · [[log|Operations Log]] · [[_backlinks|Backlinks]]

---

## Recently Updated

```dataview
TABLE updated AS "Updated", type AS "Type"
FROM "wiki"
WHERE type != "index" AND file.name != "home" AND file.name != "log"
SORT updated DESC
LIMIT 10
```

## Concepts

```dataview
LIST
FROM "wiki/concepts"
SORT title ASC
```

## Clients

### Current
```dataview
LIST
FROM "wiki/clients/current"
WHERE file.name = "_index"
SORT title ASC
```

### Prospects
```dataview
LIST
FROM "wiki/clients/prospects"
WHERE file.name = "_index"
SORT title ASC
```

### Former
```dataview
LIST
FROM "wiki/clients/former"
WHERE file.name = "_index"
SORT title ASC
```

## Knowledge

```dataview
LIST
FROM "wiki/knowledge"
WHERE file.name != "_index"
SORT title ASC
```

## Recent Activity

> [!info] Last 5 operations from [[log|wiki/log.md]]
> 
> _Check [[log]] for the full operations history._
