// ════════════════════════════════════════════════════════════════════════════
// SMART RAG — Neo4j Example Seed
// ════════════════════════════════════════════════════════════════════════════
//
// This is a SMALL EXAMPLE concept graph for an introductory research-methods
// course. It exists to demonstrate the data model — replace it with your own
// course concepts before going live.
//
// Run this AFTER schema.cypher has been applied (constraints must exist).
// Idempotent: uses MERGE, can be re-run safely.
//
// To customise:
//   1. Replace topic names below with your own course chapters / units.
//   2. Replace concept names + descriptions with your own learning concepts.
//   3. Update the BELONGS_TO relationships to map each concept to its topic.
//   4. Update the PREREQUISITE_FOR relationships to reflect actual dependencies.
//
// Tip: keep concept `name` short and stable — it is used as the lookup key
//      by the Flowise "Load neo4j Prerequisites" function (CONTAINS match
//      against the current_topic from each conversation).
// ════════════════════════════════════════════════════════════════════════════


// ─── 1. TOPICS ───────────────────────────────────────────────────────────────

MERGE (t:Topic {name: "Research Foundations"});
MERGE (t:Topic {name: "Quantitative Methods"});
MERGE (t:Topic {name: "Qualitative Methods"});


// ─── 2. CONCEPTS ─────────────────────────────────────────────────────────────

// Topic 1: Research Foundations
MERGE (c:Concept {name: "Research Question"})
SET c.chapter = 1, c.section_id = "1.1",
    c.description = "A focused, answerable question that drives a study. Defines scope and method choice.";

MERGE (c:Concept {name: "Hypothesis"})
SET c.chapter = 1, c.section_id = "1.2",
    c.description = "A testable prediction derived from theory or prior evidence. Bridges question and method.";

MERGE (c:Concept {name: "Operationalisation"})
SET c.chapter = 1, c.section_id = "1.3",
    c.description = "Translating abstract constructs into measurable variables. Essential for empirical work.";

// Topic 2: Quantitative Methods
MERGE (c:Concept {name: "Sampling"})
SET c.chapter = 2, c.section_id = "2.1",
    c.description = "Selecting participants from a population. Random vs. convenience, power considerations.";

MERGE (c:Concept {name: "Descriptive Statistics"})
SET c.chapter = 2, c.section_id = "2.2",
    c.description = "Summarising data via central tendency, dispersion, distribution shape.";

MERGE (c:Concept {name: "Inferential Statistics"})
SET c.chapter = 2, c.section_id = "2.3",
    c.description = "Drawing conclusions about populations from sample data. Significance tests, confidence intervals.";

// Topic 3: Qualitative Methods
MERGE (c:Concept {name: "Interview Design"})
SET c.chapter = 3, c.section_id = "3.1",
    c.description = "Structuring open-ended conversations to elicit rich data. Structured, semi-structured, unstructured.";

MERGE (c:Concept {name: "Thematic Analysis"})
SET c.chapter = 3, c.section_id = "3.2",
    c.description = "Identifying patterns of meaning across qualitative data. Coding, theme refinement.";

MERGE (c:Concept {name: "Triangulation"})
SET c.chapter = 3, c.section_id = "3.3",
    c.description = "Combining multiple data sources or methods to strengthen findings.";


// ─── 3. CONCEPT → TOPIC RELATIONSHIPS ────────────────────────────────────────

MATCH (c:Concept {name: "Research Question"}),      (t:Topic {name: "Research Foundations"})  MERGE (c)-[:BELONGS_TO]->(t);
MATCH (c:Concept {name: "Hypothesis"}),             (t:Topic {name: "Research Foundations"})  MERGE (c)-[:BELONGS_TO]->(t);
MATCH (c:Concept {name: "Operationalisation"}),     (t:Topic {name: "Research Foundations"})  MERGE (c)-[:BELONGS_TO]->(t);

MATCH (c:Concept {name: "Sampling"}),               (t:Topic {name: "Quantitative Methods"})  MERGE (c)-[:BELONGS_TO]->(t);
MATCH (c:Concept {name: "Descriptive Statistics"}), (t:Topic {name: "Quantitative Methods"})  MERGE (c)-[:BELONGS_TO]->(t);
MATCH (c:Concept {name: "Inferential Statistics"}), (t:Topic {name: "Quantitative Methods"})  MERGE (c)-[:BELONGS_TO]->(t);

MATCH (c:Concept {name: "Interview Design"}),       (t:Topic {name: "Qualitative Methods"})   MERGE (c)-[:BELONGS_TO]->(t);
MATCH (c:Concept {name: "Thematic Analysis"}),      (t:Topic {name: "Qualitative Methods"})   MERGE (c)-[:BELONGS_TO]->(t);
MATCH (c:Concept {name: "Triangulation"}),          (t:Topic {name: "Qualitative Methods"})   MERGE (c)-[:BELONGS_TO]->(t);


// ─── 4. PREREQUISITE RELATIONSHIPS ───────────────────────────────────────────
// Pattern: (foundational concept)-[:PREREQUISITE_FOR]->(downstream concept)

// Foundations chain
MATCH (a:Concept {name: "Research Question"}),      (b:Concept {name: "Hypothesis"})              MERGE (a)-[:PREREQUISITE_FOR]->(b);
MATCH (a:Concept {name: "Hypothesis"}),             (b:Concept {name: "Operationalisation"})      MERGE (a)-[:PREREQUISITE_FOR]->(b);

// Foundations → Quantitative
MATCH (a:Concept {name: "Operationalisation"}),     (b:Concept {name: "Sampling"})                MERGE (a)-[:PREREQUISITE_FOR]->(b);
MATCH (a:Concept {name: "Sampling"}),               (b:Concept {name: "Descriptive Statistics"})  MERGE (a)-[:PREREQUISITE_FOR]->(b);
MATCH (a:Concept {name: "Descriptive Statistics"}), (b:Concept {name: "Inferential Statistics"})  MERGE (a)-[:PREREQUISITE_FOR]->(b);

// Foundations → Qualitative
MATCH (a:Concept {name: "Research Question"}),      (b:Concept {name: "Interview Design"})        MERGE (a)-[:PREREQUISITE_FOR]->(b);
MATCH (a:Concept {name: "Interview Design"}),       (b:Concept {name: "Thematic Analysis"})       MERGE (a)-[:PREREQUISITE_FOR]->(b);

// Cross-method synthesis
MATCH (a:Concept {name: "Inferential Statistics"}), (b:Concept {name: "Triangulation"})           MERGE (a)-[:PREREQUISITE_FOR]->(b);
MATCH (a:Concept {name: "Thematic Analysis"}),      (b:Concept {name: "Triangulation"})           MERGE (a)-[:PREREQUISITE_FOR]->(b);


// ─── 5. SUMMARY ──────────────────────────────────────────────────────────────
// Run this to verify the seed worked:
//
//   MATCH (t:Topic) RETURN count(t) AS topics;         // expect: 3
//   MATCH (c:Concept) RETURN count(c) AS concepts;     // expect: 9
//   MATCH ()-[r:BELONGS_TO]->() RETURN count(r);       // expect: 9
//   MATCH ()-[r:PREREQUISITE_FOR]->() RETURN count(r); // expect: 9
