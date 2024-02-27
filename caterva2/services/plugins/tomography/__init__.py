import io
import pathlib

import blosc2
from PIL import Image

from fastapi import FastAPI, Request, responses
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ... import srv_utils


app = FastAPI()
BASE_DIR = pathlib.Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

name = 'tomography' # Identifies the plugin
contenttype = 'tomography'


cache = None

def init(sub_cache):
    print('INIT', sub_cache)
    global cache
    cache = sub_cache

@app.get("/display/{path:path}", response_class=HTMLResponse)
def display(
    request: Request,
    # Path parameters
    path: pathlib.Path,
):

    abspath = srv_utils.cache_lookup(cache, cache / path)
    arr = blosc2.open(abspath)

    context = {
        "name": name,
        "path": path,
    }
    context['pages'] = arr.shape[0]
    response = templates.TemplateResponse(request, "display.html", context=context)
    return response


@app.get("/image/{path:path}")
async def image_file(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    # Query parameters
    i: int
):

    abspath = srv_utils.cache_lookup(cache, cache / path)
    arr = blosc2.open(abspath)

    img = arr[i,:]
    img = Image.fromarray(img)
    if img.mode != 'RGB':
        img = img.convert('RGB')

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)

    def iterfile():
        yield from img_byte_arr

    return responses.StreamingResponse(iterfile(), media_type='image/png')
