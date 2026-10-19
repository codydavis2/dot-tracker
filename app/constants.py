"""Constants shared by more than one blueprint."""

# Vehicle inspection checklist. Item text is copied onto each submitted report,
# so changing this list only affects inspections filled out afterward.
INSPECTION_CHECKLIST = [
    "Oil", "Coolant", "Belts", "Exhaust", "Intake", "Fuel",
    "Springs", "Shocks",
    "Brakes", "ABS", "Tires", "Rims", "Frame", "Lights", "Electrical",
    "Reflectors", "Windshield", "Steering", "Battery", "Coupling Devices",
    "Horn", "Mirrors", "Error Codes", "Misc.",
]
# inspection_reports.inspection_type -> label. Who may submit each type is in
# permissions.INSPECTION_TYPE_ROLES; which checklist each uses is CHECKLISTS below.
INSPECTION_TYPE_LABELS = {"pre_trip": "Pre-trip", "monthly": "Monthly", "annual": "Annual", "general": "General"}
INSPECTION_STATUS_LABELS = {"good": "Good", "needs_attention": "Needs Attention", "out_of_spec": "Out of Spec"}

ROLE_LABELS = {"owner": "Owner", "manager": "Manager", "mechanic": "Mechanic", "driver": "Driver"}
# Roles an owner can give through invites and the Users page. Never "owner":
# ownership transfer is its own flow (M3).
ASSIGNABLE_ROLES = ("manager", "mechanic", "driver")
