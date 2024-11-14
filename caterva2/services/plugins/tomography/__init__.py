import pathlib

# Requirements
import numpy
import PIL.Image
from fastapi import Depends, FastAPI, Request, responses
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Project
from caterva2.services.sub import optional_user

from ...sub import get_container, open_b2, resize_image
from ...sub import templates as sub_templates
from ...subscriber import db

app = FastAPI()
BASE_DIR = pathlib.Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

name = "tomography"  # Identifies the plugin
label = "Tomography"
contenttype = "tomography"


abspath_and_dataprep = None
urlbase = None


def init(absp_n_datap, urlbase_):
    global abspath_and_dataprep
    global urlbase
    abspath_and_dataprep = absp_n_datap
    urlbase = urlbase_


def url(path: str) -> str:
    return f"{urlbase}/{path}"


def guess(path: pathlib.Path, meta) -> bool:
    """Does dataset (given path and metadata) seem of this content type?"""
    if not hasattr(meta, "dtype"):
        return False  # not an array

    dtype = meta.dtype

    # Structured dtype
    if isinstance(dtype, str) and dtype.startswith("["):
        dtype = eval(dtype)  # TODO Make it safer

    dtype = numpy.dtype(dtype)
    shape = tuple(meta.shape)
    if dtype.kind != "u":
        return False

    if len(shape) == 3:
        return True  # grayscale

    # RGB(A)
    return len(shape) == 4 and shape[-1] in (3, 4)


@app.get("/display/{path:path}", response_class=HTMLResponse)
def display(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    user: db.User = Depends(optional_user),
):
    abspath, _ = abspath_and_dataprep(path, user=user)
    arr = open_b2(abspath, path)

    base = url(f"plugins/{name}")
    context = {
        "href": f"{base}/display_one/{path}",
        "pages": arr.shape[0],
    }
    return templates.TemplateResponse(request, "display.html", context=context)


async def __get_image(path, user, i):
    # Alternatively, call abspath_and_dataprep with the corresponding slice to download data async
    array = await get_container(path, user)
    content = array[i]
    return PIL.Image.fromarray(content)


@app.get("/display_one/{path:path}", response_class=HTMLResponse)
async def display_one(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    # Query parameters
    i: int,
    user: db.User = Depends(optional_user),
):
    img = await __get_image(path, user, i)

    base = url(f"plugins/{name}")
    src = f"{base}/image/{path}?{i=}"

    width = 768  # Max size
    links = []
    if img.width > width:
        links.append(
            {
                "href": src,
                "label": f"{img.width} x {img.height} (original size)",
                "target": "blank_",
            }
        )
        src = f"{src}&{width=}"

    context = {"src": src, "links": links}
    return sub_templates.TemplateResponse(request, "display_image.html", context=context)


@app.get("/image/{path:path}")
async def image_file(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    # Query parameters
    i: int,
    width: int | None = None,
    user: db.User = Depends(optional_user),
):
    img = await __get_image(path, user, i)
    img_file = resize_image(img, width)

    def iterfile():
        yield from img_file

    return responses.StreamingResponse(iterfile(), media_type="image/png")
