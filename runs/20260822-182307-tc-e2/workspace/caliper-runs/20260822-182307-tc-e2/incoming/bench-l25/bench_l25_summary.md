# Bench L2.5 — matrice modelli

| modello | backend | json_ok% | **inventati%** | estratti_ok% | lat media (s) |
|---|---|---|---|---|---|
| granite4:1b | ollama | 100.0 | **51.4** | 92.9 | 1.2 |
| granite4:3b | ollama | 100.0 | **5.7** | 95.2 | 0.98 |
| qwen3:8b | ollama | 100.0 | **2.9** | 100.0 | 18.94 |
| llama3.1:8b | ollama | 100.0 | **2.9** | 100.0 | 1.56 |
| gpt-4o-mini | openai | 100.0 | **0.0** | 95.2 | 1.11 |

Soglia di candidatura: inventati=0, json_ok=100, estratti_ok >= baseline granite4:1b.
