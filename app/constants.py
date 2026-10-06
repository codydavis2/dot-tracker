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
INSPECTION_STATUS_LABELS = {"good": "Good", "needs_attention": "Needs Attention", "out_of_spec": "Out of Spec"}
