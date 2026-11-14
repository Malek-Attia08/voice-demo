"""The prompt: CareVoice's medical voice assistant."""
import unittest

from worker import prompts


class InstructionsTests(unittest.TestCase):
    def test_the_assistant_is_mira_and_knows_nothing_on_its_own(self):
        text = prompts.voice_instructions("en")
        self.assertIn("You are Mira, the voice assistant of the CareVoice clinic, talking with the patient out loud.", text)
        self.assertIn("You are not a doctor and you never diagnose.", text)
        self.assertIn("On your own you know nothing", text)

    def test_every_tool_is_named(self):
        text = prompts.voice_instructions("en")
        for name in ("rag_answer", "history", "medical_info", "get_time", "find_appointment_slots", "end_call"):
            self.assertIn(name, text)

    def test_the_conversation_rules_are_kept(self):
        text = prompts.voice_instructions("en")
        for kept in (
            "greetings, thanks and goodbyes",
            "can you repeat that",
            "never guess",
            "Keep every fact, number and name as",
            "add no facts of your own",
            "never say you will check, look at, fix, improve or do something",
            "apologise in one sentence",
            "While you wait for a tool you may say something natural, or stay silent.",
            "Never mention tools",
            "The user may interrupt you at any moment: stop and listen.",
            "pick up exactly where they stopped hearing you",
        ):
            self.assertIn(kept, text)

    def test_medical_answers_are_retold_without_adding_or_diagnosing(self):
        text = prompts.voice_instructions("en")
        self.assertIn("medical_info", text)
        self.assertIn("never diagnose", text)
        self.assertIn("emergency", text)

    def test_the_language_rule_names_the_language(self):
        self.assertIn("Speak Dutch: open the conversation in it and keep to it. Only if the user speaks to you in another "
                      "language, answer in theirs.", prompts.voice_instructions("nl"))
        self.assertIn("Speak French", prompts.voice_instructions("fr-BE"))

    def test_an_unknown_language_gets_the_generic_rule(self):
        self.assertIn("- Speak the language the user speaks.", prompts.voice_instructions("xx"))
        self.assertNotIn("open the conversation in it", prompts.voice_instructions("xx"))

    def test_hanging_up_is_told_how(self):
        self.assertIn("call end_call to hang up", prompts.voice_instructions("en"))


class GreetingTests(unittest.TestCase):
    def test_the_call_opens_with_an_introduction_in_the_calls_language_and_no_tool(self):
        text = prompts.greeting("nl")
        self.assertIn("Introduce yourself as Mira from the CareVoice clinic in one short, warm sentence in Dutch and ask how you can help.", text)
        self.assertIn("Do not call any tool for this.", text)

    def test_an_unknown_language_has_no_language_in_the_greeting(self):
        self.assertNotIn(" in None", prompts.greeting("xx"))
        self.assertIn("Introduce yourself as Mira from the CareVoice clinic in one short, warm sentence and ask", prompts.greeting("xx"))


if __name__ == "__main__":
    unittest.main()
