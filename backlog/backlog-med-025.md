# Backlog Item Template

**ID:** backlog-med-025
**Priority:** MED
**Status:** COMPLETED
**Created:** 2026-02-19
**Updated:** 2026-02-20

---

## Summary

Notification system improvements - add health check alerts, transition failure notifications, and configurable preferences.

---

## Description

The current notification system handles mode transitions, daily summaries, and manual button presses. However, there are several gaps that could improve user awareness of system behavior:

### Missing Notifications

1. **Health Check Corrections** - When the state machine's health check detects hardware drift and corrects the mode, users aren't notified. This can be confusing when the mode suddenly changes.

2. **Failed Transition Alerts** - When a mode transition fails (command to Powerwall rejected), no notification is sent. Users may think automation is working when it's not.

3. **Automation Disable Notification** - When the automation_enabled switch is turned off, no notification is sent.

4. **Manual Override Timeout** - When manual override auto-clears after the timeout period, no notification is sent.

### Improvement Opportunities

5. **Notification Preferences** - Add switch entities to enable/disable specific notification types (e.g., `notify_transitions`, `notify_daily_summary`)

6. **Persistent Fallback** - If the notify service fails, fall back to persistent notifications

7. **Branding Consistency** - Change "Powerwall:" prefix to "LocalShift:" in notification titles

8. **Dry Run Indicator** - Include "[Dry Run]" in notifications when dry run mode is active

---

## Affected Files

- `custom_components/localshift/notification_service.py` - Core notification logic
- `custom_components/localshift/state_machine.py` - Health check and transition handling
- `custom_components/localshift/coordinator.py` - Daily summary scheduling
- `custom_components/localshift/const.py` - New switch entity constants

---

## Proposed Solution

### Phase 1: Critical Alerts (High Priority)

1. Add notification method for health check corrections:
```python
async def send_health_check_notification(
    self, mode: BatteryMode, data: CoordinatorData
) -> None:
    """Notify when health check corrects hardware drift."""
```

2. Add notification method for failed transitions:
```python
async def send_transition_failed_notification(
    self, target_mode: BatteryMode, error: str, data: CoordinatorData
) -> None:
    """Notify when a mode transition fails."""
```

### Phase 2: User Preferences (Medium Priority)

3. Add new switch entities in config flow:
   - `notify_mode_transitions` (default: ON)
   - `notify_daily_summary` (default: ON)
   - `notify_manual_actions` (default: ON)

4. Update NotificationService to check preferences before sending

### Phase 3: Polish (Low Priority)

5. Change "Powerwall:" to "LocalShift:" in notification titles
6. Add "[Dry Run]" prefix when dry_run switch is ON
7. Add try/except with persistent notification fallback

---

## Notes

- Documentation already created in `docs/NOTIFICATIONS.md`
- Consider adding notification preferences as switch entities (similar to existing automation_enabled)
- Health check corrections happen silently every minute - users may find this confusing
- The "Powerwall:" prefix appears in multiple places and should be consistent with rebrand

---

## Related Items

- docs/NOTIFICATIONS.md (documentation - created)
- backlog-med-024 (settings usability - related UI work)
