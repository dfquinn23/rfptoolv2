# rfptoolv2

RFP automation tool — second generation.

## Architecture

```
Incoming RFP
     │
     ▼
[1] Question Detector (LLM agent)
     │
     ▼
[2] Qdrant Vector Search (past RFP answers)
     │
     ▼
[3] LLM Reranker (semantic match scoring — no generation)
     │
     ├── score ≥ 0.85 → ✅ Auto-insert
     ├── score 0.65–0.85 → ⚠️ Insert with review flag
     └── score < 0.65 → 🔬 Company docs RAG fallback
                              └── no match → 🚫 Human queue
```

## Setup

```bash
conda activate rfptoolv2
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
streamlit run app/main.py
```

## Project Structure

```
rfptoolv2/
├── app/            ← Streamlit entry point
├── core/           ← Config, Qdrant client, logger
├── ingest/         ← Data cleaning & vectorization pipeline
├── pipeline/       ← Question detection, matching, routing
├── ui/             ← Streamlit pages and components
├── tests/
└── data/           ← past_rfps/, company_docs/, output/, logs/
```
