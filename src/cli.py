import argparse

# Requirements
import httpx


HOST = 'localhost:8000'
name = None

async def handle_event(data, topic):
    print(f'{topic=} {data=}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--sub', action='append', default=[])
    parser.add_argument('-n', '--name')
    args = parser.parse_args()

    # List
    print('List of resources...')
    response = httpx.get(f'http://{HOST}/publishers')
    response.raise_for_status()
    json = response.json()
    print(json)
