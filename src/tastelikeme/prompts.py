DESCRIBE = """You are a creative director building a taste canon. Below are saved items. For EACH item,
write one rich description a designer could recreate the piece from. Cover:
- subject: what is depicted or said
- palette: dominant colors, contrast, temperature
- composition: framing, layout, hierarchy, negative space
- mood: the feeling and era or genre it evokes
- why it works: the specific choice that makes it good
Be concrete and specific. No generic praise. Keep each under 120 words.
{vision}
Save each result with:
  tastelikeme describe --id <id> --text "<description>"
or in bulk (JSON lines {{"id": ..., "description": ...}} on stdin):
  tastelikeme describe --stdin

ITEMS:
{items}
"""
VISION_IMAGE = ("For items of kind 'image' you must LOOK at the image (open the file path or fetch the URL with "
                "your vision capability). If you cannot see images, describe only from title/text and mark the "
                "description starting with '[text-only]'.")
VISION_TEXT = "These items are text, so no vision is needed."

DISTILL = """You are distilling a taste rubric from verdicts. Current rubric:
{rubric}

New verdicts since the last distillation (up = liked, down = disliked, with the specific feedback):
{verdicts}

Write an updated rubric as 5-12 short bullet rules in this form:
- DO ... (evidence: liked piece ids)
- AVOID ... (evidence: disliked piece ids)
Keep earlier rules that still hold, revise or drop ones the new verdicts contradict. Be specific (palette,
layout, pacing, tone). Then save it:
  tastelikeme rubric set --text "<rubric>" {cluster_flag}
"""

FOLLOWUP = "Which part missed - palette, layout, pacing, subject, or tone? One line is enough."
