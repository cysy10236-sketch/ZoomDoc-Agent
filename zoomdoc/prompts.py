from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zoomdoc.config import BBoxFormat
    from zoomdoc.schemas import CropAsset

SEEKER_PROMPT_TEMPLATE = """You are an assistant that locates evidence in document pages. The input is ONE image composed of several LOW-RESOLUTION page thumbnails arranged in a grid and separated by black lines. Small text may be unreadable; judge relevance from layout, headings, tables, figures and any legible words.

Images are numbered from zero, counting left to right, then top to bottom.

## Question
{question}

## Response Format
Respond with JSON only:
{
    "reason": "Evaluate the relevance of each thumbnail to the question step by step",
    "summary": "Information related to the question that is legible in the thumbnails",
    "choice": [<image numbers>]
}

## Example
Question: What is the departure time of the train from Hangzhou to Beijing?
{
    "reason": "Image 0 is a weather report. Image 1 looks like a ticket with a route line from Hangzhou to Beijing; the time field is too small to read. Image 2 is an airport timetable, not related.",
    "summary": "A train ticket for Hangzhou to Beijing exists; its time field needs closer reading.",
    "choice": [1]
}

## Rules
- Select every image that may be related, even weakly.
- The summary only contains question-related information.
- Do not mention image numbers in the summary.
- Return JSON only.

{page_map}
"""

INSPECTOR_PROMPT_TEMPLATE = """You are an assistant that answers questions from document images. Each image is preceded by a label stating whether it is a FULL PAGE or a ZOOMED CROP of a page.

## Task
Choose exactly ONE action:
- "answer": the images are sufficient. Answer and list the images you used.
- "zoom": the needed information is on one of the images but too small or blurry to read reliably. Request a high-resolution crop of that region.
- "seek": the images are insufficient. State what information is missing and keep only the related images.

## Question
{question}

## Zoom budget
Remaining zoom operations: {zoom_left}. If it is 0, do not choose "zoom".

## Answer Format (JSON only, one of the following)
A. {
    "action": "answer",
    "reason": "Solve the question step by step",
    "answer": "Brief answer in several words",
    "reference": [<image numbers>]
}
B. {
    "action": "zoom",
    "reason": "Why the region must be enlarged",
    "image": <image number to zoom into>,
    "bbox_2d": [x_min, y_min, x_max, y_max],
    "target": "What you expect to read in the region"
}
   {bbox_instruction}
C. {
    "action": "seek",
    "reason": "Evaluate each image's relevance one by one, then reason step by step",
    "information": "The specific information still required",
    "choice": [<image numbers that are related>]
}

## Examples
{"action": "answer", "reason": "The process diagram ends with an output labeled purity 42%.", "answer": "42%", "reference": [0]}
{"action": "zoom", "reason": "Image 1 contains the fee table, but the digits in the last row are illegible at this resolution.", "image": 1, "bbox_2d": [120, 610, 880, 700], "target": "the total fee in the last table row"}
{"action": "seek", "reason": "The images describe the campaign goals but not its success metrics.", "information": "Success metrics of the campaign.", "choice": [0]}

Images are numbered from 0 to {max_index} in the order they are given.
Return JSON only.
"""

INSPECTOR_PROMPT_M2 = """You are an assistant that answers questions from document images. Each image is preceded by a label stating whether it is a FULL PAGE or a ZOOMED CROP of a page.

## Task
Choose exactly ONE action:
- "answer": the images are sufficient. Answer, provide your confidence, and list the images you used.
- "zoom": the needed information is on one of the images but too small or blurry to read reliably. Request a high-resolution crop of that region.
- "seek": the images are insufficient. State what information is missing and keep only the related images.

## Question
{question}
{feedback}
## Zoom budget
Remaining zoom operations: {zoom_left}. If it is 0, do not choose "zoom".

## Answer Format (JSON only, one of the following)
A. {
    "action": "answer",
    "reason": "Solve the question step by step",
    "answer": "Brief answer in several words",
    "confidence": <float 0-1, your probability that the answer is correct>,
    "reference": [<image numbers>]
}
B. {
    "action": "zoom",
    "reason": "Why the region must be enlarged",
    "image": <image number to zoom into>,
    "bbox_2d": [x_min, y_min, x_max, y_max],
    "target": "What you expect to read in the region"
}
   {bbox_instruction}
C. {
    "action": "seek",
    "reason": "Evaluate each image's relevance one by one, then reason step by step",
    "information": "The specific information still required",
    "choice": [<image numbers that are related>]
}

## Examples
{"action": "answer", "reason": "The process diagram ends with an output labeled purity 42%.", "answer": "42%", "confidence": 0.95, "reference": [0]}
{"action": "zoom", "reason": "Image 1 contains the fee table, but the digits in the last row are illegible at this resolution.", "image": 1, "bbox_2d": [120, 610, 880, 700], "target": "the total fee in the last table row"}
{"action": "seek", "reason": "The images describe the campaign goals but not its success metrics.", "information": "Success metrics of the campaign.", "choice": [0]}

Images are numbered from 0 to {max_index} in the order they are given.
Return JSON only.
"""

SYNTHESIZER_PROMPT_TEMPLATE = """You are an assistant that answers questions from document images. Answer using the images and the related information, and list the images you used.

## Question
{question}
{related_information_section}
## Answer Format (JSON only)
{
    "reason": "Solve the question step by step",
    "answer": "Brief answer in several words",
    "reference": [<image numbers>]
}

Images are numbered from 0 to {max_index} in the order they are given.
"""

SYNTHESIZER_TEXT_PROMPT = """You are an assistant that synthesizes answers from textual evidence reports collected from document pages.

## Question
{question}

## Collected Evidence
{evidence_block}

## Response Format
Respond with JSON only:
{
    "reason": "Synthesize the answer based on the evidence reports step by step",
    "answer": "Clear, concise final answer",
    "reference_pages": [<page numbers used as evidence>]
}

Return JSON only.
"""


def render_evidence_block(reports: list[Any]) -> str:
    """Format structured evidence reports into pure text without any image paths."""
    lines: list[str] = []
    for rep in reports:
        conf_str = f"{rep.confidence:.2f}" if rep.confidence is not None else "None"
        ans_str = rep.answer if rep.answer is not None else "None"
        lines.append(
            f"[Round {rep.round}] status={rep.status}, confidence={conf_str}, "
            f"pages={rep.reference_pages}, answer={ans_str}, reason={rep.reason}"
        )
    return "\n".join(lines)


def bbox_instruction(fmt: BBoxFormat) -> str:
    if fmt == "xyxy_1000":
        return "bbox_2d uses coordinates normalized to 0-1000 relative to THAT image: (0,0) is its top-left corner, (1000,1000) its bottom-right corner. Make the box tight around the region but include the full text line or table cell."
    elif fmt == "yxyx_1000":
        return "bbox_2d uses [y_min, x_min, y_max, x_max] coordinates normalized to 0-1000 relative to THAT image: (0,0) is its top-left corner, (1000,1000) its bottom-right corner."
    elif fmt == "xyxy_abs":
        return "bbox_2d uses absolute pixel coordinates [x_min, y_min, x_max, y_max] relative to THAT image."
    else:
        return "bbox_2d uses coordinates normalized to 0-1000 relative to THAT image."


def label_full_page(idx: int, page: int) -> str:
    return f"Image {idx}: FULL PAGE {page} (medium resolution)."


def label_crop(idx: int, crop: CropAsset) -> str:
    b = crop.page_box
    return (
        f"Image {idx}: ZOOMED CROP of page {crop.page}, region "
        f"[{round(b.x1 * 1000)}, {round(b.y1 * 1000)}, {round(b.x2 * 1000)}, {round(b.y2 * 1000)}] "
        f"(x_min, y_min, x_max, y_max in 0-1000 page coordinates). Requested to read: {crop.target}."
    )
