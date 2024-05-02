import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List

from databricks import sql


class DeltaTableRetriever:

    def __init__(self, server_hostname: str, http_path: str, access_token: str):
        self.server_hostname = server_hostname
        self.http_path = http_path
        self.access_token = access_token

    async def query(self, cutoff: datetime) -> List[Dict[str, any]]:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor() as pool:
            return await loop.run_in_executor(pool, self._fetch_data, cutoff)

    def _fetch_data(self, cutoff: datetime) -> List[Dict[str, any]]:
        print(f"querying data with cutoff date: {cutoff.isoformat()}")

        data = []
        with sql.connect(server_hostname=self.server_hostname, http_path=self.http_path, access_token=self.access_token) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM kelvin.pcp_optimization_recommendations WHERE timestamp >= ?", (cutoff,))
                result = cursor.fetchall()

                print(f"query returned {len(result)} rows")

                for row in result:
                    data.append(row.asDict())

        return data
