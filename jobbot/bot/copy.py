"""Core copy: onboarding, menu and global replies. Modules carry their own strings."""

BRAND = "JobBot"

WELCOME = (
    f"Welcome to {BRAND}. I can find you jobs, track your applications, answer common "
    "questions and put you in touch with our team, all here on WhatsApp.\n\nFirst, what's your name?"
)
ASK_NOTIFY = (
    "Thanks {name}. May I message you when something happens for you, such as a new matching "
    "job or an update on an application? You can reply STOP at any time."
)
ONBOARDING_DONE = "You're all set, {name}."
NOTIFY_ON = "Notifications are on. Reply STOP at any time to pause them."
NOTIFY_OFF = "Notifications are off. You can still use everything from the menu."

MENU_BODY = "Hi {name}. What would you like to do?"
MENU_LIST_BUTTON = "Open menu"
MENU_UNKNOWN = "I didn't catch that. Please pick an option below."
MENU_UNSUPPORTED = "I can only read text and button taps for now. Please pick an option below."
OPT_OUT = ("Notifications paused. You can still use the menu any time. "
           "Tap below to turn notifications back on.")

BTN_YES = "Yes, please"
BTN_NO = "Not now"
BTN_MENU = "Main menu"
BTN_NOTIFY_ON = "Notifications on"
