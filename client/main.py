import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from config.config import config
from utils.ehentai import get_download_url, get_GP_cost
from utils.status import GP_usage_log, get_status

logger.add("log.log", encoding="utf-8")


app = FastAPI()


@app.post("/resolve")
async def resolve(request: Request):
    try:
        data = await request.json()
        gid = data["gid"]
        token = data["token"]
        image_quality = data['image_quality']
        require_GP = int(await get_GP_cost(gid, token, image_quality))
        if config["ehentai"]["max_GP_cost"] == 0 and require_GP > 0:
            msg = "Rejected"
            d_url = None
        else:
            d_url = await get_download_url(gid, token, image_quality)
            msg = "Success"
            if config["ehentai"]["max_GP_cost"] > 0:
                GP_usage_log.append((time.time(), require_GP))
        logger.info(
            f"{data['username']} 归档 https://e-hentai.org/g/{gid}/{token}/  需要{require_GP} GP  {msg}"
        )
        return JSONResponse(
            content={
                "msg": msg,
                "d_url": d_url,
                "require_GP": require_GP,
                "status": await get_status(),
            }
        )
    except Exception as e:
        logger.error(e)
        return JSONResponse(content={"msg": "Failed", "status": await get_status()})


@app.get("/status")
async def status():
    return JSONResponse(content={"status": await get_status()})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=None, port=4655)
