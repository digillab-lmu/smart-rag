// ════════════════════════════════════════════════════════════════════════════
// SMART RAG — Neo4j Schema (Constraints)
// ════════════════════════════════════════════════════════════════════════════
//
// This file defines the data model used by the concept prerequisite graph.
// It is run automatically by scripts/bootstrap.sh on first deploy and is safe
// to re-run (constraints use IF NOT EXISTS).
//
// Data model:
//   (Topic)     — a high-level topic in the course (chapter / unit)
//   (Concept)   — a specific learning concept (theory, method, idea)
//   (:Concept)-[:BELONGS_TO]->(:Topic)
//   (:Concept)-[:PREREQUISITE_FOR]->(:Concept)
//
// Concept properties used by the Flowise "Load neo4j Prerequisites" function:
//   - name         (required, unique)  — the concept identifier
//   - chapter      (int, optional)     — topic/chapter number for ordering
//   - section_id   (text, optional)    — stable section identifier (e.g. "2.3")
//   - description  (text, optional)    — short explanation
//
// To populate the graph with your course content, edit and run
// neo4j/seed.example.cypher as a template, or write your own seed script.
// ════════════════════════════════════════════════════════════════════════════


// ─── Uniqueness constraints ──────────────────────────────────────────────────
// These also accelerate MERGE lookups significantly.

CREATE CONSTRAINT constraint_topic_name IF NOT EXISTS
FOR (t:Topic) REQUIRE t.name IS UNIQUE;

// A concept is unique WITHIN ITS COURSE, not across the installation. The
// constraint used to be on c.name alone, which made the multi-course graph
// impossible: two courses cannot both have a concept called "Cognitive
// Load", and the second one fails on MERGE.
//
// The pair (course_id, name) would be a node key, and node keys are Neo4j
// Enterprise only
// (neo4j.com/docs/cypher-manual/current/constraints/managing-constraints).
// This runs Community, so the pair is carried in one synthetic property —
// c.key = "<course_id>::<name>" — and that property is unique. Written by
// the Content Admin on every MERGE, never by hand.
DROP CONSTRAINT constraint_concept_name IF EXISTS;

CREATE CONSTRAINT constraint_concept_key IF NOT EXISTS
FOR (c:Concept) REQUIRE c.key IS UNIQUE;
