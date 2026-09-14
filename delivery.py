"""One delivery coordinator for every transport; no Discord dependency.

prepare(item) may be synchronous or asynchronous and must do all validation,
destination resolution and payload construction before any remote message send.
send(item, payload) is the uncertainty boundary: any exception/cancellation after
entering it is conservatively uncertain, including timeouts and lost responses.
"""
import asyncio
import inspect

import storage


class DeliveryCoordinator:
    def __init__(self, prepare, send):
        self.prepare = prepare
        self.send = send

    async def deliver_one(
        self, item_id: int, *, approve: bool = False, actor: str = "herald",
        expected_revision: int | None = None, expected_status: str | None = None,
    ) -> dict:
        claimed = storage.claim_item(
            item_id, approve=approve, actor=actor,
            expected_revision=expected_revision, expected_status=expected_status,
        )
        if claimed is None:
            current = storage.get_item_by_id(item_id)
            return {"id": item_id, "status": "blocked" if current else "missing",
                    "current_status": current["status"] if current else "missing",
                    "error": "Item changed, is in flight, or lacks current revision approval" if current else "Item not found"}
        token = claimed["claim_token"]
        revision = claimed["revision"]
        entered_send = False
        try:
            payload = self.prepare(claimed)
            if inspect.isawaitable(payload):
                payload = await payload
            current = storage.get_item_by_id(item_id)
            if (current is None or current["status"] != "sending"
                    or current["claim_token"] != token or current["revision"] != revision
                    or current["approval_revision"] != revision
                    or current["content_digest"] != claimed["content_digest"]
                    or storage.material_digest(claimed) != claimed["content_digest"]):
                raise RuntimeError("Claim no longer authorises this send")
            # No owner or rediscovery mutation can rewrite a claimed row.
            entered_send = True
            message_id = await self.send(claimed, payload)
            if not str(message_id or "").strip():
                raise RuntimeError("Send returned no delivery receipt")
            if not storage.mark_item_posted(
                item_id, str(message_id), claim_token=token, revision=revision,
            ):
                raise RuntimeError("Delivery receipt could not be committed to its claim")
            return {"id": item_id, "status": "posted", "message_id": str(message_id), "error": ""}
        except BaseException as exc:
            # Store a bounded error classification, never exception text containing
            # credentials, private source URLs, or provider-controlled content.
            error = ("Send acceptance unknown: " if entered_send else "Preparation failed: ") + type(exc).__name__
            status = "uncertain" if entered_send else "failed"
            try:
                finish = storage.mark_item_uncertain if entered_send else storage.mark_item_failed
                if not finish(item_id, error, claim_token=token, revision=revision):
                    status = "uncertain"
                    error = "Claim changed before finalization; owner reconciliation required"
            except Exception:
                # A failed SQLite write leaves the durable sending claim for startup
                # reconciliation; never report it as a safe retryable failure.
                status = "uncertain"
                error = "Claim finalization unavailable; reconcile after restart"
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            return {"id": item_id, "status": status, "error": error}

    async def deliver_batch(
        self, limit: int = 10, *, approve: bool = False, status: str = "pending",
        actor: str = "herald",
    ) -> dict:
        limit = max(1, min(int(limit), 200))
        if status not in {"held", "pending"}:
            raise ValueError("Only held or pending rows can enter delivery")
        # Only IDs/revisions are selected here. Each iteration reloads/claims its
        # item, so skip/revision changes while an earlier send awaits take effect.
        selected = storage.list_items_by_status(status, limit, newest_first=False)
        stats = {"checked": 0, "posted": 0, "failed": 0, "uncertain": 0, "missing": 0, "blocked": 0}
        for selected_item in selected:
            stats["checked"] += 1
            try:
                result = await self.deliver_one(
                    selected_item["id"], approve=approve, actor=actor,
                    expected_status=status, expected_revision=selected_item["revision"],
                )
            except Exception:
                # Database/pre-claim errors are isolated too; no remote send was
                # attempted unless deliver_one established its durable claim.
                stats["blocked"] += 1
                continue
            stats[result["status"]] += 1
        return stats
