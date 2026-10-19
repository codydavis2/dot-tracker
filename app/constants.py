"""Constants shared by more than one blueprint."""

# Inspection checklists. Item text is copied onto each submitted report, so
# changing a list only affects inspections filled out afterward. The server picks
# the checklist from the inspection type (CHECKLISTS); the form can't change it.

# Full shop checklist: monthly, annual and general inspections.
INSPECTION_CHECKLIST = [
    "Oil", "Coolant", "Belts", "Exhaust", "Intake", "Fuel",
    "Springs", "Shocks",
    "Brakes", "ABS", "Tires", "Rims", "Frame", "Lights", "Electrical",
    "Reflectors", "Windshield", "Steering", "Battery", "Coupling Devices",
    "Horn", "Mirrors", "Error Codes", "Misc.",
]

# Driver's pre-trip walkaround (the parts a driver must be satisfied are in good
# working order before driving, 49 CFR 396.13 / 392.7).
PRE_TRIP_CHECKLIST = [
    "Service brakes (incl. trailer brake connections)",
    "Parking brake",
    "Air lines & gladhands",
    "Steering",
    "Lights & reflectors",
    "Tires",
    "Wheels & rims",
    "Hubs",
    "Horn",
    "Windshield & wipers",
    "Mirrors",
    "Coupling devices",
    "Fluid levels & leaks",
    "Emergency equipment",
]

CHECKLISTS = {
    "pre_trip": PRE_TRIP_CHECKLIST,
    "monthly": INSPECTION_CHECKLIST,
    "annual": INSPECTION_CHECKLIST,
    "general": INSPECTION_CHECKLIST,
}
# inspection_reports.inspection_type -> label. Who may submit each type is in
# permissions.INSPECTION_TYPE_ROLES; which checklist each uses is CHECKLISTS below.
INSPECTION_TYPE_LABELS = {"pre_trip": "Pre-trip", "monthly": "Monthly", "annual": "Annual", "general": "General"}
INSPECTION_STATUS_LABELS = {"good": "Good", "needs_attention": "Needs Attention", "out_of_spec": "Out of Spec"}

ROLE_LABELS = {"owner": "Owner", "manager": "Manager", "mechanic": "Mechanic", "driver": "Driver"}
# Roles that can be given through invites and the Users page. Never "owner":
# ownership transfer is its own flow (ownership.py).
ASSIGNABLE_ROLES = ("manager", "mechanic", "driver")
