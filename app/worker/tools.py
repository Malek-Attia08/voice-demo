"""Fake tools for Gemini Live in the demo: hardcoded data, no network.

`TOOLS` are the declarations (plain JSON Schema) the worker hands to Gemini; `call` runs one by name.
The clock is passed in so tests do not depend on the real time.
"""
from datetime import datetime

TOOLS = [
    {
        "name": "rag_answer",
        "description": "Look up an answer about the clinic or about the assistant itself (its role, appointments, opening hours, prescriptions, contact) in its knowledge base.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "The user's question, translated to English."}},
            "required": ["question"],
        },
    },
    {
        "name": "history",
        "description": "Read back the last turns of this conversation.",
        "parameters": {
            "type": "object",
            "properties": {"count": {"type": "integer", "description": "How many turns, default 5."}},
        },
    },
    {
        "name": "medical_info",
        "description": "General health information on a topic (fever, headache, cough, sprain, dehydration, sore throat). "
                       "Demo data, not medical advice.",
        "parameters": {
            "type": "object",
            "properties": {"topic": {"type": "string", "description": "The health topic or symptom, in the user's words."}},
            "required": ["topic"],
        },
    },
    {
        "name": "get_time",
        "description": "The current date and time.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "find_appointment_slots",
        "description": "List the next free appointment slots for a specialty (general practice, pediatrics, dermatology, "
                       "cardiology). Demo data. It only lists slots: it cannot book them.",
        "parameters": {
            "type": "object",
            "properties": {"specialty": {"type": "string", "description": "The specialty, translated to English; general practice if the user does not say."}},
        },
    },
]

# topic -> (keywords in English, French, Dutch and German, snippet); the first topic with a keyword found in the question wins.
KNOWLEDGE = {
    "about": (("who are you", "what can you do", "your role", "what do you do", "carevoice", "mira", "help me with",
               "qui es-tu", "qui êtes-vous", "qui etes-vous", "que peux-tu", "que pouvez-vous", "ton rôle", "votre rôle",
               "votre role", "que fais-tu", "que faites-vous", "wie ben je", "wie bent u", "wat kun je", "wat kunt u",
               "je rol", "uw rol", "wer bist du", "wer sind sie", "was kannst du", "was können sie", "deine rolle",
               "ihre rolle"),
              "I am Mira, the voice assistant of the CareVoice clinic. I can give the clinic's opening hours, help with "
              "appointments, prescriptions and contact details, and share general health information. I am not a doctor "
              "and I cannot diagnose; in an emergency, call the emergency number."),
    "appointment": (("appointment", "book", "cancel", "reschedule", "rendez-vous", "rdv", "annuler", "réserver",
                     "reserver", "prendre un", "afspraak", "annuleren", "boeken", "termin", "absagen", "buchen"),
                    "Appointments can be booked or cancelled up to 24 hours ahead at reception or at "
                    "appointments@example.com."),
    "hours": (("hours", "open", "close", "horaire", "ouvert", "ouverture", "fermé", "ferme", "heures", "openingstijden",
               "open", "gesloten", "geopend", "öffnungszeiten", "geöffnet", "geschlossen"),
              "The clinic is open Monday to Friday, 8:00 to 18:00, and closed on public holidays."),
    "prescription": (("prescription", "refill", "medicine", "ordonnance", "renouvel", "médicament", "medicament", "recept",
                      "herhaalrecept", "medicijn", "rezept", "medikament"),
                     "Repeat prescriptions are ready two working days after you ask for them at reception."),
    "contact": (("contact", "phone", "email", "reach", "joindre", "téléphone", "telephone", "courriel", "bereiken",
                 "telefoon", "kontakt", "erreichen", "telefon"),
                "Reception answers at reception@example.com within one working day."),
}
NOT_FOUND = "The knowledge base has nothing on that."
DEFAULT_COUNT = 5

# Demo data for medical_info: general, conservative information only, never a diagnosis.
DISCLAIMER = " This is demo information, not medical advice. A doctor or pharmacist can say what is right for you."
MEDICAL = {
    "fever": "A fever is a body temperature above about 38 degrees Celsius. Rest, drinking plenty of fluids and light "
             "clothing usually help. See a doctor if it lasts more than three days, is very high, or comes with a stiff "
             "neck, a rash or confusion.",
    "headache": "Most headaches ease with rest, water and a quiet, dark room. Regular pain relievers can help if they "
                "suit you. See a doctor if it is sudden and severe, follows a blow to the head, or comes with fever, "
                "vision changes or weakness.",
    "cough": "A cough is often from a cold and fades within two to three weeks. Warm drinks and rest can soothe it. See "
             "a doctor if it lasts longer, brings up blood, or comes with shortness of breath.",
    "sprain": "For a sprain, think rest, ice for about twenty minutes at a time, a light compression bandage and keeping "
              "the limb raised. See a doctor if you cannot put weight on it, or it is very swollen or misshapen.",
    "dehydration": "Signs include thirst, dark urine, dizziness and a dry mouth. Sip water or an oral rehydration drink "
                   "slowly. See a doctor if it comes with repeated vomiting, confusion or no urine for many hours.",
    "sore throat": "A sore throat is often viral and passes in a few days. Warm drinks, lozenges and rest help. See a "
                   "doctor if it lasts over a week, makes swallowing very hard, or comes with a high fever.",
}
MEDICAL_NOT_FOUND = "The demo health information has nothing on that."
# Anything that could be an emergency is answered with the emergency number first, whatever else was asked.
EMERGENCY_WORDS = ("chest pain", "cannot breathe", "can't breathe", "trouble breathing", "unconscious", "stroke",
                   "severe bleeding", "overdose", "suicid", "seizure", "heart attack")
EMERGENCY = ("This could be an emergency. Call the emergency number now: 112 in Europe, 911 in the US. "
             "Do not wait for more information.")


# Demo data for find_appointment_slots: (keywords in several languages, specialty, slots).
SLOTS = [
    (("general", "doctor", "gp", "généraliste", "generaliste", "médecin", "huisarts", "dokter", "allgemein", "hausarzt"),
     "general practice", "Monday 9:30, Monday 14:00, Tuesday 11:15"),
    (("pediatr", "child", "kid", "pédiatr", "enfant", "kinderarts", "kind", "kinder"),
     "pediatrics", "Wednesday 10:00, Thursday 15:30"),
    (("dermat", "skin", "peau", "huid", "haut"), "dermatology", "Friday 9:00, next Monday 16:45"),
    (("cardio", "heart", "coeur", "cœur", "hart", "herz"), "cardiology", "Thursday 8:30, next Tuesday 13:15"),
]
SLOTS_NOT_FOUND = "The clinic has no slots listed for that specialty."
SLOTS_NOTE = " This is demo information."


def _rag_answer(arguments, **_):
    question = str(arguments.get("question", "")).lower()
    for keywords, snippet in KNOWLEDGE.values():
        if any(keyword in question for keyword in keywords):
            return snippet
    return NOT_FOUND


def _medical_info(arguments, **_):
    topic = str(arguments.get("topic", "")).lower()
    if any(word in topic for word in EMERGENCY_WORDS):
        return EMERGENCY
    for keyword, snippet in MEDICAL.items():
        if keyword in topic:
            return snippet + DISCLAIMER
    return MEDICAL_NOT_FOUND + DISCLAIMER


def _history(arguments, turns=(), **_):
    if not turns:
        return "There are no earlier turns in this call."
    count = int(arguments.get("count") or DEFAULT_COUNT)
    return "\n".join(f"{who}: {text}" for who, text in list(turns)[-count:])


def _get_time(arguments, now=None, **_):
    stamp = now() if now else datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"It is {stamp}."


def _find_appointment_slots(arguments, **_):
    specialty = str(arguments.get("specialty") or "general practice").lower()
    for keywords, name, slots in SLOTS:
        if any(keyword in specialty for keyword in keywords):
            return f"Next free {name} slots: {slots}. To book one, ask reception." + SLOTS_NOTE
    return SLOTS_NOT_FOUND + SLOTS_NOTE


HANDLERS = {"rag_answer": _rag_answer, "history": _history, "medical_info": _medical_info, "get_time": _get_time, "find_appointment_slots": _find_appointment_slots}


def call(name, arguments, **context):
    """Run the tool `name`. An unknown name is an answer the model can read, not an exception."""
    handler = HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool: {name}."
    return handler(arguments or {}, **context)
