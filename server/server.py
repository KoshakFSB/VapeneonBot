from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
import uvicorn
import json

app = FastAPI()

# Состояние комнаты
ROOM = {
    "video_url": "",
    "is_playing": False,
    "current_time": 0,
    "viewers": []
}

# WebSocket для синхронизации
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ROOM["viewers"].append(websocket)
    
    try:
        while True:
            data = await websocket.receive_text()
            command = json.loads(data)
            
            if command["type"] == "play":
                ROOM["is_playing"] = True
                ROOM["current_time"] = command["time"]
            elif command["type"] == "pause":
                ROOM["is_playing"] = False
                ROOM["current_time"] = command["time"]
                
            # Рассылаем команды всем зрителям
            for viewer in ROOM["viewers"]:
                await viewer.send_text(json.dumps({
                    "type": "sync",
                    "is_playing": ROOM["is_playing"],
                    "current_time": ROOM["current_time"]
                }))
    except:
        ROOM["viewers"].remove(websocket)

# Раздаем статику
app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)