
import requests
from concurrent.futures import ThreadPoolExecutor

URL = "http://127.0.0.1:8000/orders/"

def place_order(key):
    payload = {
        "restaurant_id": 1,
        "items": [
            {
                "product_id": 1,
                "quantity": 4
            }
        ],
        "idempotency_key": key
    }

    try:
        response = requests.post(URL, json=payload, timeout=15)
        return {
            "key": key,
            "status": response.status_code,
            "body": response.json()
        }
    except requests.RequestException as error:
        return {"key": key, "error": str(error)}

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(place_order, "concurrency-test-A"),
            executor.submit(place_order, "concurrency-test-B")
        ]

        for future in futures:
            print(future.result())
