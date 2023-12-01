import httpx


def test_broker(services):
    response = httpx.get('http://localhost:8000/api/roots')
    assert response.status_code == 200
    assert response.json() == {'foo': {'name': 'foo', 'http': 'localhost:8001'}}
