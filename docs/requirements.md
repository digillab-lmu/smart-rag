# System Requirements

## Prerequisites
Before running `setup.sh`, ensure you have:
- Ubuntu 22.04 or 24.04 LTS
- Root or sudo access
- A domain name with DNS pointing to this server
- An LLM API Key (Anthropic, OpenAI, or OpenRouter)

`setup.sh` will handle the rest automatically.

## Embedding Options
### Option A: Local Embeddings
- Ollama (installed automatically by `setup.sh`)
- Recommended model: bge-m3 (~1.5 GB VRAM or CPU mode)
- Additional 4 GB RAM recommended

### Option B: API-based Embeddings
- Any OpenAI-compatible embedding endpoint

## Data Privacy
The level of data privacy compliance depends entirely on
your choice of models and providers:
- Full local deployment: no data leaves your infrastructure
- API-based providers: subject to the provider's terms
- Mixed setups: assess each component individually

Users are responsible for ensuring compliance with
applicable data protection requirements for their context.

## Minimum Hardware
- CPU: 4 Cores (8 recommended)
- RAM: 16 GB (32 GB recommended with local embeddings)
- Storage: 40 GB SSD
