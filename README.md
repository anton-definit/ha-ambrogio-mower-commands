# Ambrogio Mower Commands (Home Assistant Integration)

This is a custom [Home Assistant](https://www.home-assistant.io/) integration that lets you send **direct commands** to your Ambrogio (ZCS) robotic mower via the DeviceWISE API.

Unlike the full-featured `ha-zcs-mower` integration, this project is intentionally **minimal**:
- Focuses on **service calls** for automation (no entities, no sensors, no devices).
- Supports **one mower** (one IMEI).
- Commands are queued and retried automatically for reliability.

---

## Features

Available Home Assistant services:

- `ambrogio_mower_commands.set_profile`
- `ambrogio_mower_commands.work_now`
- `ambrogio_mower_commands.border_cut`
- `ambrogio_mower_commands.charge_now`
- `ambrogio_mower_commands.charge_until`
- `ambrogio_mower_commands.trace_position`
- `ambrogio_mower_commands.keep_out`
- `ambrogio_mower_commands.wake_up`
- `ambrogio_mower_commands.thing_find` (diagnostic)
- `ambrogio_mower_commands.thing_list` (diagnostic)

---

## Installation

1. Copy the folder `custom_components/ambrogio_mower_commands/` into your Home Assistant `custom_components/` directory.  
   Example: `/config/custom_components/ambrogio_mower_commands/`

2. Restart Home Assistant.

3. Add the integration via **Settings → Devices & Services → Add Integration** → search for **Ambrogio Mower Commands**.
   - Enter your mower's **IMEI** (15 digits, starts with `35`).
   - Optionally, enter a custom **Client name** (shown in the ZCS cloud).
   - A client key will be generated automatically.

---

## Example automations

### Start mowing immediately
```yaml
alias: Start mowing now
trigger:
  - platform: time
    at: "08:00:00"
action:
  - service: ambrogio_mower_commands.work_now
```

### Charge until 6:30 AM on weekdays
```yaml
alias: Charge overnight until morning
trigger:
  - platform: time
    at: "23:00:00"
action:
  - service: ambrogio_mower_commands.charge_until
    data:
      hours: 6
      minutes: 30
      weekday: 1   # Monday (1=Mon ... 7=Sun)
```

### Wake up and trace position
```yaml
alias: Wake mower and check position
trigger:
  - platform: state
    entity_id: input_boolean.locate_mower
    to: "on"
action:
  - service: ambrogio_mower_commands.wake_up
  - delay: "00:00:10"
  - service: ambrogio_mower_commands.trace_position
```

---

## Notes

- All API calls are serialized through a built-in **queue** to avoid conflicts.
- The integration will retry failed commands (auth/session issues are re-tried with re-authentication).
- Only **one mower (IMEI)** is supported.

---

## Disclaimer

This is an unofficial project and not affiliated with ZCS / Ambrogio. Use at your own risk.
