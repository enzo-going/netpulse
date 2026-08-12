# Contribuindo

Pull requests são bem-vindos. Para mudanças grandes, abra uma issue antes para
discutir o que você pretende alterar.

Antes de abrir o PR, a partir de `backend/`:

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest -q
```

São os mesmos passos que o CI executa, nas versões 3.11, 3.12 e 3.13 do Python.
