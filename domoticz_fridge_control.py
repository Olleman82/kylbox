import asyncio
from bleak import BleakClient, BleakError
import struct
import sys
import argparse
import aiohttp
import json

# --- Bluetooth Konstanter (behåll dina befintliga) ---
WRITE_CHAR  = "00001235-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR = "00001236-0000-1000-8000-00805f9b34fb"

# --- Domoticz Konfiguration ---
DOMOTICZ_URL = "http://192.168.1.123:8080"
DOMOTICZ_FRIDGE_TEMP_IDX = "62"
DOMOTICZ_FRIDGE_SETPOINT_IDX = "61"
DOMOTICZ_POLLING_INTERVAL = 10                # Sekunder mellan varje avläsning från Domoticz

# Globala variabler för att hålla reda på tillstånd
aiohttp_session = None
# Håller det senast kända/inställda målvärdet för kylboxen.
# Används för att jämföra med Domoticz och undvika onödiga uppdateringar.
current_known_setpoint = None
last_reported_fridge_temp = None
last_reported_fridge_target = None


def calculate_checksum(data: bytes) -> int:
    """Beräknar checksumma för ett meddelande"""
    return sum(data) & 0xFF

def create_packet(data: bytes) -> bytes:
    """Skapar ett paket med header, längd, payload och 2-byte checksum."""
    # data här är Kommando + Ev. DataPayload
    pkt_payload_len = len(data)
    # Längd-byten i protokollet är (längd på kommando+datapayload) + (längd på checksum = 2)
    length_byte_value = pkt_payload_len + 2
    pkt = b'\xFE\xFE' + struct.pack('B', length_byte_value) + data
    # Checksumman beräknas på (Header + LängdByte + Kommando + DataPayload)
    csum = sum(pkt) & 0xFFFF
    pkt += struct.pack('>H', csum)
    return pkt

def parse_frame(data: bytearray):
    # enkel ram-parser
    # Längd-byten (data[2]) är (längd på kommando+datapayload) + (längd på checksum = 2)
    # Så, längden på (kommando + datapayload) är data[2] - 2
    cmd_plus_payload_len = data[2] - 2
    cmd = data[3]
    # DataPayload börjar efter kommando-byten och är (cmd_plus_payload_len - 1) bytes lång (om cmd_plus_payload_len > 0)
    payload_len = cmd_plus_payload_len - 1
    if payload_len > 0:
        payload = data[4 : 4 + payload_len]
    else:
        payload = b''
    return cmd, payload

# --- Domoticz Funktioner ---
async def update_domoticz_device(idx: str, svalue: str, nvalue: int = 0):
    """Uppdaterar en enhet i Domoticz."""
    global aiohttp_session
    if not idx or idx in ["IDX_TEMP", "IDX_SETPOINT"]: # Kollar om IDX är default placeholder
        print(f"Domoticz: Ogiltigt IDX ('{idx}'). Uppdatera konfigurationen.")
        return
    if not aiohttp_session:
        print("Domoticz: Aiohttp session är inte initialiserad.")
        return

    url = f"{DOMOTICZ_URL}/json.htm?type=command&param=udevice&idx={idx}&nvalue={nvalue}&svalue={svalue}"
    try:
        async with aiohttp_session.get(url) as response:
            if response.status == 200:
                response_json = await response.json()
                if response_json.get("status") == "OK":
                    print(f"Domoticz: Enhet {idx} uppdaterad till svalue={svalue}.")
                else:
                    print(f"Domoticz: Fel vid uppdatering av enhet {idx}: {response_json.get('status')}")
            else:
                print(f"Domoticz: HTTP-fel {response.status} vid uppdatering av enhet {idx}.")
    except aiohttp.ClientError as e:
        print(f"Domoticz: Kunde inte ansluta för att uppdatera enhet {idx}: {e}")
    except json.JSONDecodeError:
        html_response = await response.text()
        print(f"Domoticz: Fel vid avkodning av JSON-svar från Domoticz för enhet {idx}. Svar: {html_response[:200]}")


async def get_domoticz_setpoint(idx: str) -> float | None:
    """Hämtar börvärdet från en Domoticz-enhet (typiskt 'Setpoint')."""
    global aiohttp_session
    if not idx or idx == "IDX_SETPOINT": # Kollar om IDX är default placeholder
        print(f"Domoticz: Ogiltigt IDX ('{idx}') för setpoint. Uppdatera konfigurationen.")
        return None
    if not aiohttp_session:
        print("Domoticz: Aiohttp session är inte initialiserad.")
        return None

    url = f"{DOMOTICZ_URL}/json.htm?type=devices&rid={idx}"
    try:
        async with aiohttp_session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("status") == "OK" and data.get("result"):
                    device_info = data["result"][0]
                    # För "Setpoint" (General -> Setpoint) är värdet i 'Data'
                    # För vissa andra enheter kan det vara 'svalue1' eller 'SetPoint'
                    setpoint_str = device_info.get("Data")
                    if setpoint_str is None:
                         setpoint_str = device_info.get("svalue1") # Fallback
                    if setpoint_str is None:
                         setpoint_str = device_info.get("SetPoint") # Fallback för termostatenheter

                    if setpoint_str:
                        try:
                            # Ta bort eventuell enhet (t.ex. "25.0 C" -> "25.0")
                            return float(setpoint_str.split(" ")[0])
                        except ValueError:
                            print(f"Domoticz: Kunde inte tolka setpoint '{setpoint_str}' som ett tal för IDX {idx}.")
                            return None
                    else:
                        print(f"Domoticz: Inget 'Data', 'svalue1' eller 'SetPoint' fält hittades för IDX {idx}. Enhetsinfo: {device_info}")
                        return None
                else:
                    print(f"Domoticz: Fel vid hämtning av enhet {idx}: {data.get('status')}")
                    return None
            else:
                print(f"Domoticz: HTTP-fel {response.status} vid hämtning av enhet {idx}.")
                return None
    except aiohttp.ClientError as e:
        print(f"Domoticz: Kunde inte ansluta för att hämta enhet {idx}: {e}")
        return None
    except (json.JSONDecodeError, IndexError) as e:
        html_response = await response.text()
        print(f"Domoticz: Fel vid avkodning/tolkning av JSON ({e}) från Domoticz för enhet {idx}. Svar: {html_response[:200]}")
        return None

# --- Kylbox Kommunikation ---
def notification_handler(_, data):
    global current_known_setpoint, last_reported_fridge_temp, last_reported_fridge_target
    cmd, payload = parse_frame(data)

    if cmd == 0x01:  # query-svar
        try:
            actual_temp = struct.unpack('b', bytes([payload[14]]))[0]
            target_temp = struct.unpack('b', bytes([payload[4]]))[0]
            bat_percent = payload[15]
            bat_vol = f"{payload[16]}.{payload[17]}"

            print(f"Kylbox: Aktuell temp: {actual_temp}°C, Måltemp: {target_temp}°C, Batteri: {bat_percent}% ({bat_vol}V)")
            # print(f"Debug - payload (Query): {[hex(x) for x in payload]}")

            if DOMOTICZ_FRIDGE_TEMP_IDX != "IDX_TEMP" and last_reported_fridge_temp != actual_temp:
                asyncio.create_task(update_domoticz_device(DOMOTICZ_FRIDGE_TEMP_IDX, str(actual_temp)))
                last_reported_fridge_temp = actual_temp

            if DOMOTICZ_FRIDGE_SETPOINT_IDX != "IDX_SETPOINT" and last_reported_fridge_target != target_temp:
                asyncio.create_task(update_domoticz_device(DOMOTICZ_FRIDGE_SETPOINT_IDX, str(target_temp)))
                last_reported_fridge_target = target_temp
            
            current_known_setpoint = target_temp
        except IndexError:
            print(f"Kylbox: Fel vid tolkning av query-svar (payload för kort?). Payload: {[hex(x) for x in payload]}")
        except Exception as e:
            print(f"Kylbox: Oväntat fel i notification_handler (query): {e}")


    elif cmd == 0x05:  # set-temperature bekräftelse (eko)
        print("Kylbox: Bekräftelse (eko) mottagen för Set Temperature.")
        # Egentligen innehåller denna payload det vi skickade. Vi förlitar oss på nästa query-svar för att se effekten.
        # print(f"Debug - payload (Set Confirm/Echo): {[hex(x) for x in payload]}")


async def connect_with_retry(address, max_attempts=3):
    for attempt in range(max_attempts):
        try:
            print(f"Försöker ansluta till {address} (försök {attempt + 1}/{max_attempts})...")
            client = BleakClient(address)
            await client.connect()
            print(f"Ansluten till {address}!")
            return client
        except BleakError as e:
            print(f"Anslutningsfel: {e}")
            if attempt < max_attempts - 1:
                print("Väntar 5 sekunder innan nästa försök...")
                await asyncio.sleep(5)
            else:
                print("Kunde inte ansluta efter alla försök.")
                return None

async def set_temperature(client, temp: int):
    global current_known_setpoint, last_reported_fridge_target
    if not client or not client.is_connected:
        print("Kylbox: Klienten är inte ansluten. Kan inte sätta temperatur.")
        return

    # Kommando 0x05 (SetUnit1Target)
    # Data för create_packet är [kommando, temp_byte]
    temp_byte = struct.pack('b', temp)[0]
    command_payload = bytes([0x05, temp_byte])
    command_to_send = create_packet(command_payload)
    
    print(f"Kylbox: Skickar kommando för att sätta temp till {temp}°C: {[hex(x) for x in command_to_send]}")
    try:
        await client.write_gatt_char(WRITE_CHAR, command_to_send)
        print(f"Kylbox: Kommando för att sätta temperatur till {temp}°C skickat.")
        
        current_known_setpoint = temp
        if DOMOTICZ_FRIDGE_SETPOINT_IDX != "IDX_SETPOINT" and last_reported_fridge_target != temp:
             asyncio.create_task(update_domoticz_device(DOMOTICZ_FRIDGE_SETPOINT_IDX, str(temp)))
             last_reported_fridge_target = temp

        await asyncio.sleep(1) 

        print("Kylbox: Verifierar temperaturändring genom ny query...")
        # Kommando 0x01 (Query), ingen ytterligare data payload
        query_command_payload = bytes([0x01])
        query_to_send = create_packet(query_command_payload)
        await client.write_gatt_char(WRITE_CHAR, query_to_send)
        # Svaret hanteras av notification_handler, vänta lite på det.
        await asyncio.sleep(2)

    except BleakError as e:
        print(f"Kylbox: Fel vid sändning av temperaturkommando: {e}")


async def poll_domoticz_for_setpoint_changes(client):
    global current_known_setpoint
    print("Startar Domoticz polling för ändringar i börvärde...")
    while True:
        await asyncio.sleep(DOMOTICZ_POLLING_INTERVAL)
        if not client or not client.is_connected:
            # print("Domoticz Polling: Kylbox-klient inte ansluten, pausar tillfälligt.")
            continue # Fortsätt loopa, klienten kan återansluta

        if DOMOTICZ_FRIDGE_SETPOINT_IDX == "IDX_SETPOINT":
            # print("Domoticz Polling: IDX för setpoint är inte konfigurerad.")
            continue


        domoticz_sp_float = await get_domoticz_setpoint(DOMOTICZ_FRIDGE_SETPOINT_IDX)

        if domoticz_sp_float is not None:
            new_target_temp_from_domoticz = int(round(domoticz_sp_float))

            if current_known_setpoint is None or new_target_temp_from_domoticz != current_known_setpoint:
                print(f"Domoticz: Nytt börvärde upptäckt: {new_target_temp_from_domoticz}°C (tidigare känt: {current_known_setpoint}°C). Sätter ny temp på kylbox.")
                await set_temperature(client, new_target_temp_from_domoticz)
            # else:
            #     print(f"Domoticz: Börvärde ({new_target_temp_from_domoticz}°C) oförändrat jämfört med känt ({current_known_setpoint}°C).")


async def main():
    global aiohttp_session, current_known_setpoint
    
    parser = argparse.ArgumentParser(description='Styr kylboxen och synkronisera med Domoticz')
    parser.add_argument('--address', type=str, default="07:4D:FB:A7:C4:5E", help='Bluetooth-adress till kylboxen (standard: 07:4D:FB:A7:C4:5E)')
    parser.add_argument('-t', '--temp', type=int, help='Sätt initial temperatur (°C) för kylboxen vid start')
    args = parser.parse_args()

    aiohttp_session = aiohttp.ClientSession()
    
    client = await connect_with_retry(args.address)
    if not client:
        print("Kunde inte ansluta till kylboxen. Avslutar.")
        if aiohttp_session: await aiohttp_session.close()
        sys.exit(1)

    polling_task = None
    try:
        await client.start_notify(NOTIFY_CHAR, notification_handler)
        
        print("Skickar initial query för att hämta status från kylboxen...")
        initial_query_payload = bytes([0x01]) # Kommando 0x01
        initial_query_command = create_packet(initial_query_payload)
        await client.write_gatt_char(WRITE_CHAR, initial_query_command)
        print("Väntar på initial status från kylboxen...")
        await asyncio.sleep(3) 

        if args.temp is not None:
            print(f"Sätter initial temperatur till {args.temp}°C via kommandorad...")
            await set_temperature(client, args.temp)
        
        # Vänta lite till om current_known_setpoint inte hunnit sättas
        if current_known_setpoint is None:
             print("Väntar lite extra på att måltemperatur ska läsas från kylboxen...")
             await asyncio.sleep(5) 

        if current_known_setpoint is not None:
            print(f"Initialt känt börvärde för kylbox efter setup: {current_known_setpoint}°C")
        else:
            print("Kunde inte fastställa initialt börvärde från kylboxen. Domoticz polling kan vara osynkad initialt.")
            # Fallback, om du vill ha ett defaultvärde om inget kan läsas
            # current_known_setpoint = 4 

        polling_task = asyncio.create_task(poll_domoticz_for_setpoint_changes(client))
        print("Huvudloop aktiv. Tryck Ctrl+C för att avsluta.")
        
        # Håll huvudtråden levande
        while True:
            await asyncio.sleep(3600) # Sov en lång stund, låt tasks göra jobbet

    except KeyboardInterrupt:
        print("\nAvbryter programmet...")
    except Exception as e:
        print(f"Ett oväntat fel uppstod i main: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Påbörjar nedstängning...")
        if polling_task and not polling_task.done():
            print("Avbryter Domoticz polling-task...")
            polling_task.cancel()
            try:
                await polling_task 
            except asyncio.CancelledError:
                print("Domoticz polling-task avbruten.")
            except Exception as e_pt:
                 print(f"Fel vid avslut av polling_task: {e_pt}")
        
        if client and client.is_connected:
            print("Kopplar från kylboxen...")
            try:
                #await client.stop_notify(NOTIFY_CHAR) # Kan ge fel om redan frånkopplad
                await client.disconnect()
                print("Frånkopplad från kylboxen.")
            except BleakError as e:
                print(f"Fel vid frånkoppling från kylbox: {e}")
            except Exception as e_dc:
                 print(f"Oväntat fel vid frånkoppling: {e_dc}")

        if aiohttp_session and not aiohttp_session.closed:
            print("Stänger aiohttp-session...")
            await aiohttp_session.close()
            print("Aiohttp-session stängd.")
        
        print("Programmet har avslutats.")

if __name__ == "__main__":
    asyncio.run(main()) 