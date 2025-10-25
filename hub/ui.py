import os
import markdown
from fastapi import (
    APIRouter,
    responses,
    # Request,
    Response,
    # status,
    # WebSocket,
    # HTTPException,
)
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)
router = APIRouter()




@router.get('/')
async def redir():
    return responses.RedirectResponse(url='/welcome')


@router.get('/{page}')
async def main_pages(page:str):
    md_file_names={
        'welcome': 'index.md',
        'show_login_key': 'showloginkey.md',
        'configure_router': 'configurerouter.md',
        'bases': 'baseslist.md',
        'login': 'login.md',
    }
    static_files={
        'css.css':{ # Web path
            'path':'css.css', # file name/path
            'file_type':'text/css',# For HTTP response type
        },
        'code_field.js':{
            'path':'code_field.js',
            'file_type':'text/JavaScript',
        },
        'corner_menue.js':{
            'path':'corner_menue.js',
            'file_type':'text/JavaScript',
        },
    }
    if page in md_file_names.keys():
        fileName=md_file_names[page]
        current_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(current_dir, 'ui', fileName)
        file=open(path,'r')
        mdStr=file.read()
        file.close()
        outerHTMLFile=open(os.path.join(current_dir, 'ui', 'outer-HTML.html'))
        out=""
        for line in outerHTMLFile.readlines():
            if '<!--markdown-here-->' in line:
                out+=markdown.markdown(mdStr)
            else:
                out+=line
        outerHTMLFile.close()
        return Response(out,media_type='text/html')
    elif page in static_files.keys():
        current_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(current_dir, 'ui', static_files[page]['path'])
        return responses.FileResponse(path, media_type=static_files[page]['file_type'])
    elif page =='favicon.ico':
        current_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(current_dir), 'app','images','BitBurrow.png')   #app/images/BitBurrow.png
        return responses.FileResponse(path, media_type="image/png")
    else:
        return Response(content="Not Found", status_code=404)





def init_ui(api_router):
    api_router.include_router(router)
