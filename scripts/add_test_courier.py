import os
from src import db

def run():
    tg_id = 5793256116
    name = "Тестовый курьер"
    
    # Проверим есть ли уже
    existing = db.get_courier_by_tg(tg_id)
    if existing:
        print(f"Courier {tg_id} already exists. Updating status to 'free'...")
        db.set_courier_status(tg_id, "free")
    else:
        print(f"Registering new courier {tg_id}...")
        db.get_db().table("couriers").insert({
            "tg_id": tg_id,
            "name": name,
            "status": "free"
        }).execute()
    print("Done!")

if __name__ == "__main__":
    run()
