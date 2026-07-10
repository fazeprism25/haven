from obsidian.memory_engine.query_rewriter import QueryRewriter

queries = [
    "What should I build next for the project?",
    "What database did the user choose for Project Atlas?",
    "Which CI/CD tool did the user decide to use?",
    "What system is being used for configuration and secret management?",
    "What does the user currently believe about Rust?",
]

rewriter = QueryRewriter()

for query in queries:
    result = rewriter.rewrite(query)

    print("=" * 80)
    print("Original:")
    print(result.original)
    print()

    for i, rewrite in enumerate(result.rewrites, start=1):
        print(f"Rewrite {i}: {rewrite}")

    if not result.rewrites:
        print("No rewrites produced.")