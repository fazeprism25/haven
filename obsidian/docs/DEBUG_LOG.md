# Debug Log

## 2026-06-27

### Core
- Fixed Source → SourceType export mismatch.
- Fixed UpdateType → OperationType export mismatch.
- Synchronized core package exports with current API.

### Demo
- Demo now successfully loads a Conversation.
- ManagerPipeline is executing.
- Current blocker: demo StubLLM returns extractor JSON for every pipeline stage.

- Fixed demo StubLLM to match Extractor validation schema.
- Fixed demo StubLLM to match Classifier validation schema.
- Fixed demo StubLLM to match Importance validation schema.