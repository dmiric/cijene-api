## Brief overview
This guideline defines a strict rule regarding the execution of Docker commands that remove containers or volumes.

## Docker command execution
-   **Explicit Approval for Removal:** Never execute any Docker commands that will remove containers or volumes without explicit user approval. This includes commands like `docker compose down -v`, `docker system prune`, or any other command that leads to the deletion of Docker resources.
-   **Approval Trigger:** Always ask for explicit user approval before running such commands, even if they are part of a larger automated process (e.g., `make rebuild-everything`).
