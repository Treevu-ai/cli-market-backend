#!/usr/bin/env python3
import asyncio
import json
import os
import asyncpg


async def main():
    url = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(url, ssl="prefer")
    rows = await conn.fetch(
        """
        SELECT id, started_at, finished_at, stores_attempted, stores_succeeded,
               prices_collected, LEFT(COALESCE(errors::text, ''), 300) AS errors_preview
        FROM collector_runs ORDER BY id DESC LIMIT 10
        """
    )
    for r in rows:
        print(json.dumps({k: str(v) if v is not None else None for k, v in dict(r).items()}))
    stuck = await conn.fetchval(
        "SELECT COUNT(*) FROM collector_runs WHERE finished_at IS NULL"
    )
    print("stuck_runs", stuck)
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())