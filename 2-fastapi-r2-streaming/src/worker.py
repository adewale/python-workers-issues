from workers import WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # FastAPI's telemetry needs entropy, which is only available in a request.
        import asgi
        from app import app

        return await asgi.fetch(app, request.js_object, self.env)
