#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sphero BOLT — Autorun (oneindig) met automatische kalibratie, wijzerszin
- Geen joystick nodig
- Auto-calibratie: heading-offset & snelheidsfactor (indien locator beschikbaar)
- Start automatisch na 3 s (countdown)
- Blijft rondjes rijden tot je Ctrl+C doet

Benodigd:
  pip install spherov2 bleak dbus-next
Gebruik:
  python3 sphero_autorun.py SB-7740
"""

import sys
import time
import math
from spherov2 import scanner
from spherov2.types import Color
from spherov2.sphero_edu import SpheroEduAPI
from spherov2.commands.power import Power

# ---------- Instellingen ----------
COUNTDOWN_S = 3            # 3-2-1 countdown
PAUSE_SEG   = 0.07         # stabilisatiepauze na elk segment

# Snelheidsmodel: bij speed≈170 rijdt de BOLT ~0.60 m/s (stel bij na 1 testrun).
# Als auto-calibratie slaagt, wordt dit bijgesteld.
M_PER_S_AT_170 = 0.60

# Snelheden
STRAIGHT_SPEED = 180       # snelheid op rechte stukken
TURN_SPEED     = 140       # snelheid in bochten

# Heading-offset in graden (soft correctie).
# Als auto-calibratie slaagt, wordt dit dynamisch gezet.
# Als jouw startoriëntatie "omhoog" is, kun je dit handmatig op -90 zetten.
HEADING_OFFSET = 0.0

# Waypoints in METERS (elk paneel = 0.50 m)
# Oorsprong (0,0) = midden van de start/finish-tegel.
# +x naar RECHTS, +y naar BENEDEN (zoals op de foto).
WAYPOINTS = [
    (0.00, 0.00),  # start/finish
    (2.20, 0.00),  # bovenlangs naar rechts
    (2.35, 2.35),  # rechts naar beneden
    (1.95, 2.00),  # boog naar links-in
    (1.55, 1.65),
    (1.20, 1.45),
    (0.80, 1.30),  # door het midden linkswaarts
    (0.60, 0.70),  # omhoog richting linksboven
    (0.35, 0.25),  # vlak voor bovenrand
    (0.00, 0.00),  # finish
]

# ---------- Hulpfuncties ----------
def heading_deg(p0, p1):
    """0° = rechts; +y is omlaag, dus gebruik -dy."""
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    return math.degrees(math.atan2(-dy, dx)) % 360

def dist_m(p0, p1):
    return math.hypot(p1[0] - p0[0], p1[1] - p0[1])

def apply_offset(hdg):
    """Pas de automatische heading-offset toe (soft-aim)."""
    return (hdg + HEADING_OFFSET) % 360

def show_countdown(api, seconds=3):
    for n in range(seconds, 0, -1):
        api.set_matrix_character(str(n), Color(0, 255, 0))
        time.sleep(1.0)
    api.set_matrix_character(">", Color(0, 255, 0))

def battery_led(api, toy):
    """Kleine batterij-indicator via front-LED (optioneel)."""
    try:
        v = Power.get_battery_voltage(toy)
        if v > 4.1:
            api.set_front_led(Color(0, 255, 0))
        elif v > 3.9:
            api.set_front_led(Color(255, 255, 0))
        elif v > 3.7:
            api.set_front_led(Color(255, 128, 0))
        else:
            api.set_front_led(Color(255, 0, 0))
        print(f"[INFO] Battery: {v:.2f} V")
    except Exception:
        pass

def supports_locator(api):
    # Sommige builds van spherov2 hebben get_location(), andere niet.
    return hasattr(api, "get_location") and callable(getattr(api, "get_location"))

def try_reset_locator(api):
    # Niet alle builds hebben reset_locator(); probeer alternatieven.
    try:
        api.reset_locator()           # best case
        return True
    except AttributeError:
        pass
    for fname in ("set_location", "set_locator", "set_position"):
        try:
            getattr(api, fname)(0, 0)   # mm of units; doel is nulpunt
            return True
        except Exception:
            continue
    return False

def auto_calibrate(api, pulse_speed=120, pulse_time=0.70):
    """
    Automatische kalibratie:
      - Indien locator beschikbaar: puls vooruit (heading 0°) en meet dx,dy
        -> bereken HEADING_OFFSET en stel M_PER_S_AT_170 bij.
      - Zonder locator: sla over en gebruik vaste HEADING_OFFSET.
    """
    global HEADING_OFFSET, M_PER_S_AT_170

    print("[CAL] Auto-calibratie…")
    api.set_speed(0)
    api.set_matrix_character("C", Color(255, 255, 0))  # C = Calibrate
    time.sleep(0.2)

    if not supports_locator(api):
        print("[CAL] Locator niet beschikbaar → kalibratie overgeslagen (gebruik vaste HEADING_OFFSET).")
        api.set_matrix_character("✓", Color(0, 200, 255))
        time.sleep(0.3)
        return

    if not try_reset_locator(api):
        print("[CAL] Locator kon niet gereset worden → kalibratie overgeslagen (vaste HEADING_OFFSET).")
        api.set_matrix_character("✓", Color(0, 200, 255))
        time.sleep(0.3)
        return

    # Puls vooruit op heading 0°
    api.set_heading(0)
    api.set_speed(pulse_speed)
    time.sleep(pulse_time)
    api.set_speed(0)
    time.sleep(0.25)

    # Positie lezen (mm -> m)
    try:
        loc = api.get_location()
        dx = (loc.get('x', 0.0)) / 1000.0
        dy = (loc.get('y', 0.0)) / 1000.0
    except Exception:
        print("[CAL] get_location() mislukt → kalibratie overgeslagen (vaste HEADING_OFFSET).")
        api.set_matrix_character("✓", Color(0, 200, 255))
        time.sleep(0.3)
        return

    dist = math.hypot(dx, dy)
    if dist < 0.02:
        print("[CAL] Te weinig verplaatsing gemeten → kalibratie overgeslagen (vaste HEADING_OFFSET).")
        api.set_matrix_character("✓", Color(0, 200, 255))
        time.sleep(0.3)
        return

    # Werkelijke bewegingshoek (wat er gebeurt als we 0° vragen)
    angle_moved = (math.degrees(math.atan2(-dy, dx)) % 360)

    # Corrigeer offset zodat 0° fysiek naar rechts (+x) wijst
    HEADING_OFFSET = (-angle_moved) % 360

    # Snelheidsmodel bijstellen (m/s bij speed=170), met begrenzing
    v_meas = dist / pulse_time
    M_PER_S_AT_170 = max(0.45, min(0.85, v_meas * 170.0 / pulse_speed))

    print(f"[CAL] dx={dx:.3f} m, dy={dy:.3f} m, angle={angle_moved:.1f}°, "
          f"OFFSET={HEADING_OFFSET:.1f}°, M170≈{M_PER_S_AT_170:.2f} m/s")

    api.set_matrix_character("✓", Color(0, 200, 255))
    time.sleep(0.3)

def run_lap(api):
    """Rijdt één volledige ronde volgens WAYPOINTS (met offset-correctie)."""
    api.set_matrix_character("A", Color(0, 255, 0))  # A = Auto
    t0 = time.time()
    prev_hdg = None

    for i in range(1, len(WAYPOINTS)):
        p0, p1 = WAYPOINTS[i-1], WAYPOINTS[i]
        hdg = heading_deg(p0, p1)
        d   = dist_m(p0, p1)

        # trager in bochten
        if prev_hdg is None:
            spd = STRAIGHT_SPEED
        else:
            turn = abs((hdg - prev_hdg + 540) % 360 - 180)  # kleinste draai
            spd = TURN_SPEED if turn > 20 else STRAIGHT_SPEED

        v_ms = M_PER_S_AT_170 * (spd / 170.0)
        dur  = d / max(v_ms, 1e-6)

        cmd_hdg = apply_offset(hdg)
        api.set_heading(int(cmd_hdg))
        api.set_speed(int(spd))
        time.sleep(dur)
        api.set_speed(0)
        time.sleep(PAUSE_SEG)

        prev_hdg = hdg
        print(f"  segment {i}: hdg_req={int(hdg)}° cmd={int(cmd_hdg)}° d={d:.2f} m v={spd}")

    lap_time = time.time() - t0
    api.set_matrix_character("✓", Color(0, 255, 0))
    print(f"[FINISH] Rondetijd: {lap_time:.2f} s")
    time.sleep(0.2)

# ---------- Hoofdprogramma ----------
def pick_toy_by_name_or_scan(target_name: str):
    """Probeer exact op naam, anders pak eerste SB- die we vinden."""
    toy = scanner.find_toy(toy_name=target_name)
    if toy:
        print(f"[INFO] Gevonden via naam: {target_name}")
        return toy
    print("[WARN] Niet direct gevonden. Scannen naar Sphero's…")
    toys = scanner.find_toys()
    if not toys:
        print("[ERR] Geen Sphero's gevonden bij scan.")
        return None
    # exacte match
    for t in toys:
        try:
            if (t.name or "").strip() == target_name:
                print(f"[INFO] Exacte match in scan: {t.name}")
                return t
        except Exception:
            pass
    # anders eerste SB-
    for t in toys:
        if (t.name or "").startswith("SB-"):
            print(f"[INFO] Neem dichtstbijzijnde: {t.name}")
            return t
    print("[ERR] Geen SB- devices gevonden in scan.")
    return None

def main(toy_name):
    print(f"[INFO] Zoeken naar '{toy_name}' …")
    toy = pick_toy_by_name_or_scan(toy_name)
    if toy is None:
        sys.exit(1)

    # Een paar verbindingspogingen om BLE-haperingen op te vangen
    attempts = 3
    for i in range(1, attempts + 1):
        try:
            print(f"[INFO] Verbinden (poging {i}/{attempts})…")
            with SpheroEduAPI(toy) as api:
                battery_led(api, toy)

                # 1) Automatische kalibratie (richting + snelheidsfactor, indien mogelijk)
                auto_calibrate(api)

                # 2) Countdown en oneindig rondjes rijden
                print(f"[INFO] Start over {COUNTDOWN_S} s.")
                show_countdown(api, COUNTDOWN_S)

                lap = 0
                while True:          # 🚀 nooit stoppen: blijft rondjes rijden
                    lap += 1
                    print(f"\n=== LAP {lap} ===")
                    run_lap(api)

                    # klein effectje + front-LED groen
                    api.set_led(Color(0, 0, 0))
                    api.set_front_led(Color(0, 255, 0))

                    # elke 5 rondes: batterij tonen
                    if lap % 5 == 0:
                        battery_led(api, toy)
            return  # normaal kom je hier niet, want while True
        except Exception as e:
            print("[WARN] Verbinding mislukt:", e)
            if i < attempts:
                print("[INFO] Opnieuw proberen…")
                time.sleep(2.5)
            else:
                print("[ERR] Kon niet verbinden. Controleer Bluetooth/Sphero en probeer opnieuw.")
                sys.exit(2)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Gebruik: python3 sphero_autorun.py <SB-naam>")
        print("Voorbeeld: python3 sphero_autorun.py SB-7740")
        sys.exit(1)

    toy_name = sys.argv[1]
    try:
        main(toy_name)
    except KeyboardInterrupt:
        print("\n[STOP] Handmatig gestopt met Ctrl+C.")
