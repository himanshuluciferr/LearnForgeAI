"""Shared workflow state read and written by every executor.

This is the contract that constrains all agents: each one consumes earlier fields
and fills in its own. Changing a model here changes the prompts downstream.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Sequence

from pydantic import BaseModel, Field

# Placed below the reviewer's own noise, not at the top of the range. Reviewing one
# unchanged chapter three times returned 82, 85 and 92, so a bar of 90 would have sent
# good work back on a coin flip. Real chapters measured 82-95, a hollow one 10-15, so
# anything in between separates them and 75 leaves room for a bad sample of good work.
PASSING_REVIEW_SCORE = 75

# Caps the review -> chapter regeneration loop so a persistently low score cannot spin forever.
MAX_REVISIONS = 2


class WorkflowStep(StrEnum):
    REQUIREMENT = "requirement"
    SUBJECT_ANALYSIS = "subject-analysis"
    RESEARCH = "research"
    CURRICULUM = "curriculum"
    CHAPTER = "chapter"
    # Sits here, not at the end, so a rewrite loop does not drag practice, project and quiz
    # round with it. They read finished chapters and only ever need to run once.
    REVIEW = "review"
    PRACTICE = "practice"
    PROJECT = "project"
    QUIZ = "quiz"
    PUBLISHER = "publisher"


STEP_ORDER: tuple[WorkflowStep, ...] = tuple(WorkflowStep)

# Rough share of total runtime, used to report progress. Must sum to 100.
STEP_WEIGHTS: dict[WorkflowStep, int] = {
    WorkflowStep.REQUIREMENT: 5,
    # Real retrieval: one search, a page fetch each for two or three sources, two model calls.
    WorkflowStep.SUBJECT_ANALYSIS: 5,
    WorkflowStep.RESEARCH: 10,
    WorkflowStep.CURRICULUM: 10,
    WorkflowStep.CHAPTER: 30,
    # One call per chapter plus a whole-syllabus pass, so it costs more than the 5 it started with.
    WorkflowStep.REVIEW: 11,
    WorkflowStep.PRACTICE: 8,
    WorkflowStep.PROJECT: 10,
    WorkflowStep.QUIZ: 8,
    WorkflowStep.PUBLISHER: 3,
}


def progress_percent(completed: Sequence[WorkflowStep]) -> int:
    return min(100, sum(STEP_WEIGHTS[step] for step in completed))


class ExperienceLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class StatedExperience(StrEnum):
    """What the learner's message revealed about their level.

    Kept apart from `ExperienceLevel` because it has a fourth value: the message may say
    nothing at all. Silently calling that 'beginner' is a claim the learner never made.
    """

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    UNKNOWN = "unknown"


class MissingRequirement(StrEnum):
    """What requirement-agent could not get from the message, and must ask for."""

    SKILL = "skill"
    SKILL_CHOICE = "skill_choice"


# Used when the learner gave no time commitment. Node 1 records the absence; every
# downstream node needs a number.
DEFAULT_DAILY_MINUTES = 30
# Used when the learner named no language. A course must pick one and say so: measured, a
# corpus holding both C# and Python variants of the same page produced a course that taught
# 27 C# examples and 7 Python ones, switching between topics.
DEFAULT_PROGRAMMING_LANGUAGE = "python"


class LearningRequest(BaseModel):
    """Output of requirement-agent — the raw Teams prompt turned into structure.

    This node captures the learner's intent. It does not decide anything about the subject
    itself, so every field here is an observation about the MESSAGE, never about the skill.
    """

    # Descriptions here become the JSON schema the model sees, so they steer extraction
    # more reliably than prompt text does.
    is_learning_request: bool = Field(
        description="False if the prompt is not a request to learn a skill. Other fields are then ignored."
    )
    skill: str | None = Field(
        default=None,
        description=(
            "The one specific technology, framework, platform, language, tool or subject the "
            "learner named, worded as they worded it. Null when they named none, named only a "
            "vendor, ecosystem or broad category such as 'Microsoft stuff' or 'AI', or offered "
            "a choice without making it. Never narrow a broad request into a specific product."
        ),
    )
    experience: StatedExperience = Field(
        default=StatedExperience.UNKNOWN,
        description=(
            "The learner's level with this skill. Use 'unknown' unless the message itself "
            "signals it — this is what they said, not what you would guess."
        ),
    )
    experience_evidence: str | None = Field(
        default=None,
        description=(
            "The words in the message that put `experience` above 'unknown', quoted or closely "
            "paraphrased. Null when experience is 'unknown'."
        ),
    )
    goal: str | None = Field(
        default=None,
        description="What the learner wants to be able to do, in one sentence. Null if unstated.",
    )
    daily_minutes: Annotated[int, Field(ge=5, le=480)] | None = Field(
        default=None,
        description="Minutes per day the learner said they can commit. Null if unstated.",
    )
    language: str = Field(
        default="en", description="ISO 639-1 code for the course language, e.g. 'en', 'hi'. Not the language name."
    )
    programming_language: str | None = Field(
        default=None,
        description=(
            "The programming language the learner asked for, lowercased, e.g. 'python', "
            "'csharp', 'typescript'. Only when they actually named one — null otherwise. Never "
            "infer it from the subject: most frameworks support several, and picking one for "
            "the learner is the kind of quiet choice that produces a course they cannot use."
        ),
    )
    alternatives: list[str] = Field(
        default_factory=list,
        description=(
            "Every skill the learner offered as an alternative to the others without choosing, "
            "as in 'React or Vue'. Leave this empty when they named a single skill, or several "
            "that belong in one course such as 'React with TypeScript'."
        ),
    )
    missing_requirements: list[MissingRequirement] = Field(
        default_factory=list,
        description=(
            "What must be asked for before a course can be built. 'skill' when no specific "
            "subject was named or the subject is too broad to build a course on. "
            "'skill_choice' when several were offered and none was chosen. Empty otherwise."
        ),
    )

    @property
    def assumed_level(self) -> ExperienceLevel:
        """The level the course is written for. 'unknown' becomes beginner here, in one place,
        rather than each downstream node inventing its own fallback."""
        if self.experience is StatedExperience.UNKNOWN:
            return ExperienceLevel.BEGINNER
        return ExperienceLevel(self.experience.value)

    @property
    def minutes_per_day(self) -> int:
        return self.daily_minutes or DEFAULT_DAILY_MINUTES

    @property
    def assumed_programming_language(self) -> str:
        """The language the course is written in. Unstated becomes the default here, in one
        place, so no downstream node invents its own — and so the assumption has a name."""
        return self.programming_language or DEFAULT_PROGRAMMING_LANGUAGE


class SourceDocument(BaseModel):
    """A page we fetched and read. The first thing in this pipeline that keeps the retrieved
    words rather than a link to them."""

    title: str
    url: str
    text: str
    # Set only where a provider states it. Empty means the page is language-neutral, which is
    # not the same as unknown: those pages suit any course.
    language: str = ""

    @property
    def words(self) -> int:
        return len(self.text.split())


class IdentityStatus(StrEnum):
    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"
    # We read pages and none of them describes the requested name.
    UNRECOGNISED = "unrecognised"
    # We could not read enough to judge at all. Kept apart from UNRECOGNISED because
    # "retrieval broke" and "this does not exist" are different facts that a single empty
    # list would otherwise collapse into one verdict.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class SourceKind(StrEnum):
    """How authoritative a document is for establishing identity.

    Recorded, not yet gated: a rule such as "confirmed needs one first-party source" is a
    threshold, and thresholds set without measurement are how the review bar ended up inside
    its own noise band. This field is what makes that rule measurable later.
    """

    FIRST_PARTY_DOCUMENTATION = "first_party_documentation"
    OFFICIAL_REPOSITORY = "official_repository"
    SPECIFICATION = "specification"
    REPUTABLE_SECONDARY = "reputable_secondary"
    OTHER = "other"


class TechnicalSubjectType(StrEnum):
    PROGRAMMING_LANGUAGE = "programming_language"
    SOFTWARE_FRAMEWORK = "software_framework"
    SOFTWARE_LIBRARY = "software_library"
    PLATFORM = "platform"
    SERVICE = "service"
    TOOL = "tool"
    PROTOCOL_OR_SPECIFICATION = "protocol_or_specification"
    CONCEPT_OR_PRACTICE = "concept_or_practice"
    PRODUCT_FEATURE = "product_feature"
    # A closed set with no escape hatch is a demand for the nearest neighbour.
    OTHER = "other"


class TargetedSearch(BaseModel):
    query: str = Field(description="The search to run.")
    domains: list[str] = Field(
        default_factory=list,
        description=(
            "Restrict to these hostnames, e.g. ['learn.microsoft.com'] or ['rust-lang.org']. "
            "Leave empty for a general search."
        ),
    )
    reason: str = Field(
        description=(
            "Why this search is needed — what these results are missing. About the search, "
            "never about what the subject is."
        )
    )


class SearchPlan(BaseModel):
    """Retrieval strategy only, as data rather than as tool calls, so the plan and the actions
    we take cannot diverge and every action stays ours to record.

    Deliberately says nothing about what the subject IS. That judgement belongs to the analysis
    step, which reads whole pages; this step sees only search snippets, and snippets are strong
    enough to find a subject but far too weak to identify one.
    """

    fetch: list[int] = Field(
        default_factory=list,
        description=(
            "Numbers of the results worth reading in full, strongest evidence first. Prefer "
            "first-party documentation and the project's own repository. Two or three is "
            "usually enough; never pick a result just to fill the list."
        ),
    )
    targeted_searches: list[TargetedSearch] = Field(
        default_factory=list,
        description=(
            "Only when these results leave the identity unsettled. Leave empty when what is "
            "here already answers it."
        ),
    )


class SubjectEvidence(BaseModel):
    document_index: int = Field(
        description="The number of the document that supports this, as printed. Never a URL."
    )
    source_kind: SourceKind = Field(
        description="How authoritative that document is for saying what this subject is."
    )
    supporting_claim: str = Field(
        description="What that document actually says which establishes what this subject is."
    )


class SubjectAnalysis(BaseModel):
    """Output of subject-analysis-agent — what the retrieved documents say the subject is.

    Every field is a claim about the documents, never about the world. `confidence`,
    `difficulty` and `estimated_hours` were specified, built and then measured out: across 13
    subjects x 3 runs, confidence overlapped between correct and incorrect identifications
    (correct fell to 0.80, wrong reached 0.95), difficulty returned `intermediate` for 10 of 13,
    and estimated_hours swung 40/120/40 on one subject.
    """

    identity_status: IdentityStatus = Field(
        description=(
            "confirmed when the documents describe the subject that was asked for; ambiguous "
            "when they describe several unrelated technical subjects sharing that name; "
            "unrecognised when none of them describes it; insufficient_evidence when what you "
            "were given is too thin to judge either way."
        )
    )
    canonical_name: str | None = Field(
        default=None,
        description=(
            "The name the documents themselves use for this subject, including a current name "
            "that has replaced an older one. Null unless a document states it."
        ),
    )
    subject_type: TechnicalSubjectType = Field(
        description="What kind of technical thing it is, per the documents."
    )
    description: str = Field(
        default="",
        description="One or two sentences on what it is, drawn from the documents.",
    )
    scope: list[str] = Field(
        default_factory=list,
        description=(
            "The main areas this subject covers, named as the documents name them. Short noun "
            "phrases such as 'workflows' or 'middleware', not sentences."
        ),
    )
    prerequisites: list[str] = Field(
        default_factory=list,
        description=(
            "What genuinely blocks starting. Never installation or setup steps, general "
            "computer literacy, or ordinary tools such as a text editor or a browser."
        ),
    )
    candidates: list[str] = Field(
        default_factory=list,
        description=(
            "Only when ambiguous: the distinct technical subjects the documents describe under "
            "this name. Do not choose between them."
        ),
    )
    evidence: list[SubjectEvidence] = Field(
        default_factory=list, description="Which documents established the identity, and how."
    )


class SubjectTrace(BaseModel):
    """What code actually did, as opposed to what the model planned.

    A model that searches and judges in one turn cannot be audited: an agent asked to do both
    reported that Rust does not exist on one run in three, with no sources, which is
    indistinguishable from never having searched. Counting the work ourselves is what makes a
    refusal mean something.
    """

    searches: list[str] = Field(default_factory=list)
    fetched_urls: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ResourceKind(StrEnum):
    DOCS = "docs"
    MICROSOFT_LEARN = "microsoft-learn"
    GITHUB = "github"
    VIDEO = "video"
    BLOG = "blog"


class ResearchSource(BaseModel):
    """One page the chapter writer reads from.

    Every field is filled in by code from a page we fetched. It used to be filled in by the
    model that proposed the URL, including a `summary` of a page it had never opened — so a
    "source" was the model describing its own recollection, and the writer wrote from that.
    """

    title: str
    url: str
    kind: ResourceKind
    # The point of the whole node. Without this the pipeline is not retrieval-augmented; it is
    # model memory with a bibliography stapled on.
    text: str
    rank_score: float = 0.0

    @property
    def words(self) -> int:
        return len(self.text.split())


class SourceSelection(BaseModel):
    """Response schema for research-agent: which numbered results are worth reading."""

    picks: list[int] = Field(
        default_factory=list,
        description=(
            "Numbers of the results worth reading in full, most useful first. You are choosing "
            "from the list you were given, so give numbers and never URLs."
        ),
    )


class TopicOutline(BaseModel):
    """One planned topic inside a chapter — the unit a single writer call produces.

    Titles are planned here rather than invented by the writer. Left to itself the writer
    named sections after their own job ("What you'll be able to do (and what goes wrong
    without this)"), which is what makes a page read as a filled-in template.
    """

    title: str = Field(
        description=(
            "The subject this topic covers, named as a thing rather than as a task. "
            "'Function tools and schema generation', never 'Overview' or 'How it works'."
        )
    )
    objectives: list[str] = Field(
        default_factory=list,
        description=(
            "One or two things the learner can DO after this topic. Start each with a verb "
            "and make it checkable — 'register a function tool' not 'understand tools'."
        ),
    )


class ChapterOutline(BaseModel):
    """One planned chapter: a part of the course, covered by its ordered topics."""

    number: int = Field(description="Position in the course, starting at 1.")
    title: str = Field(
        description=(
            "The area of the subject this chapter covers, stated concretely. Name the "
            "actual area rather than using a placeholder like 'Introduction'."
        )
    )
    objectives: list[str] = Field(
        default_factory=list,
        description=(
            "Two to four things the learner can DO after this chapter. Start each with a "
            "verb, and make them checkable — 'create an index' not 'understand indexing'."
        ),
    )
    topics: list[TopicOutline] = Field(
        default_factory=list,
        description=(
            "The topics this chapter is made of, in reading order. Each is one self-contained "
            "subject that a reader could look up on its own. Together they must cover the "
            "chapter's objectives and must not overlap each other."
        ),
    )


class Curriculum(BaseModel):
    """Output of curriculum-agent — the plan the whole course is built from."""

    title: str = Field(description="Course title naming the skill and the outcome it leads to.")
    summary: str = Field(
        description="Two or three sentences on what the course covers and who it is for."
    )
    chapters: list[ChapterOutline] = Field(
        description="Ordered chapters. Each must build on the previous ones, never repeat them."
    )


class DiagramKind(StrEnum):
    FLOW = "flow"
    SEQUENCE = "sequence"


class DiagramEdge(BaseModel):
    """One arrow. Both ends name a node by its exact label rather than by position, because a
    number is a claim about a list the model has to hold in its head while writing it."""

    source: str = Field(description="Label of the node the arrow leaves, copied exactly.")
    target: str = Field(description="Label of the node the arrow reaches, copied exactly.")
    label: str = Field(
        default="",
        description=(
            "What happens along this arrow, in a few words — 'returns tool call', 'on "
            "failure'. Leave empty when the arrow is simply the next step."
        ),
    )


class ChapterDiagram(BaseModel):
    """One picture of how the chapter's pieces fit together.

    Only the parts are asked for. The Mermaid itself is written by the diagram skill, because
    drawing syntax is the same kind of mechanical detail as a folder tree's box characters:
    the model knows the relationships and gets the punctuation subtly wrong.
    """

    kind: DiagramKind = Field(
        description=(
            "'flow' for a process, pipeline or set of parts that feed each other. "
            "'sequence' for who calls whom, in order, over time."
        )
    )
    title: str = Field(
        description="What the diagram shows, as a caption — 'How a request reaches the model'."
    )
    nodes: list[str] = Field(
        default_factory=list,
        description=(
            "Two to eight short labels for the boxes, or the participants for a sequence. "
            "Name real components from this chapter, never 'Step 1' or 'Process'."
        ),
    )
    edges: list[DiagramEdge] = Field(
        default_factory=list,
        description=(
            "The arrows between those nodes. Every source and target must be one of the "
            "labels above, spelled identically, or the arrow is dropped."
        ),
    )


class TopicDraft(BaseModel):
    """Response schema for one topic — the unit chapter-agent writes per call.

    The parts are asked for separately rather than as one blob so the document structure is
    rendered here instead of hoped for, and so a reader can find the same part of every
    topic in the same place.
    """

    what_it_is: str = Field(
        description=(
            "What this topic is, in Markdown. Define it plainly first, then precisely. "
            "Bold each term the first time it is defined."
        )
    )
    why_it_matters: str = Field(
        description=(
            "The engineering problem this exists to solve, and what goes wrong without it. "
            "Lead with the problem a reader would recognise, not with a restatement of the "
            "definition above."
        )
    )
    how_to_use: str = Field(
        description=(
            "How it is actually used, in Markdown: the real names, settings and steps, with "
            "fenced code blocks where the sources show them. Where two options are being "
            "compared on the same points, use a Markdown table."
        )
    )
    implementation: str = Field(
        default="",
        description=(
            "A complete, runnable example, as a single fenced code block with a language "
            "tag. Include one whenever the sources show enough to write code that would "
            "actually run. Leave empty rather than inventing an API the sources do not show."
        ),
    )
    diagram: ChapterDiagram | None = Field(
        default=None,
        description=(
            "A diagram of how this topic's pieces relate. Include one whenever the topic "
            "describes things that flow into, call, or transform one another, or that have a "
            "shape before and after some operation. Omit it when there is nothing to connect."
        ),
    )


class Topic(BaseModel):
    """A written topic, fitted back onto the position and title the curriculum planned."""

    chapter_number: int
    number: int
    title: str
    what_it_is: str
    why_it_matters: str
    how_to_use: str
    implementation: str = ""
    diagram: ChapterDiagram | None = None

    @property
    def label(self) -> str:
        return f"{self.chapter_number}.{self.number}"


class ChapterSection(BaseModel):
    """One titled part of a chapter. Asking for parts rather than one blob is what lets us
    render the Markdown structure ourselves instead of hoping for it."""

    heading: str = Field(
        description=(
            "Short heading naming what this part covers, e.g. 'Defining the fields'. "
            "No numbering and no generic labels like 'Introduction'."
        )
    )
    markdown: str = Field(
        description=(
            "This section's content in Markdown: short paragraphs, lists, and fenced code "
            "blocks with a language tag on every example. Do not write a heading — the "
            "heading above is rendered for you."
        )
    )
    takeaway: str = Field(
        default="",
        description=(
            "One sentence stating the rule or fact this section exists to establish, pulled "
            "out so it can be found again at a glance. Leave empty where the section is a "
            "worked example that carries no separate rule."
        ),
    )


class ChapterDraft(BaseModel):
    """Response schema for the per-chapter wrap call.

    The body is written topic by topic, so this call only produces what has to be judged
    across the whole chapter once every topic in it is written.
    """

    key_points: list[str] = Field(
        description=(
            "Three to six one-line takeaways worth revising later. State facts and rules, "
            "not a table of contents of the chapter."
        )
    )
    exercises: list[str] = Field(
        description=(
            "Two to four tasks the learner performs themselves, each one checkable by "
            "looking at the result. Give the task only, never the answer."
        )
    )


class Chapter(BaseModel):
    """A written chapter: its outline's number and title, plus the topics it is made of."""

    number: int
    title: str
    body_markdown: str
    # The same content as body_markdown, kept structured so a card or a rewrite can address
    # one topic without re-parsing the rendered page.
    topics: list[Topic] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    # Do-it-now tasks with no answer shipped. Anything with a worked answer is a PracticeItem.
    exercises: list[str] = Field(default_factory=list)
    # Optional with a default, so courses stored before diagrams existed still load.
    diagram: ChapterDiagram | None = None


class PracticeKind(StrEnum):
    """Multiple choice is deliberately absent — machine-marked questions are quiz-agent's."""

    RECALL = "recall"
    APPLY = "apply"
    BUILD = "build"
    DIAGNOSE = "diagnose"


class PracticeTask(BaseModel):
    """Response shape for practice-agent. The chapter is already known, so it is attached
    afterwards rather than asked for."""

    kind: PracticeKind = Field(
        description=(
            "What the learner has to do: recall it from memory, apply it to a scenario, "
            "build something, or diagnose something broken."
        )
    )
    prompt: str = Field(
        description=(
            "The task, stated so the learner knows when they are done. Name the specific "
            "thing to produce. Never offer options to choose between."
        )
    )
    solution: str = Field(
        description=(
            "The worked answer a learner marks themselves against: the answer itself, then "
            "one line on the part people get wrong. Never a single word, and never a "
            "re-teaching of the chapter they have just read."
        )
    )


class PracticeSet(BaseModel):
    """Response schema for practice-agent: one chapter's worth of practice."""

    tasks: list[PracticeTask] = Field(
        description=(
            "One task per learning objective, in the order the objectives were given. "
            "Vary the kind across the set rather than making them all recall."
        )
    )


class PracticeItem(BaseModel):
    """One practice task tied to a chapter.

    Unlike a chapter exercise it always ships a worked solution; unlike a quiz question it
    is judged by the learner rather than marked by the machine.
    """

    chapter_number: int
    kind: PracticeKind
    prompt: str
    solution: str


class ProjectDraft(BaseModel):
    """One project in the ramp.

    The difficulty is its position in the list, so it is not asked for, and the folder tree
    is drawn from `files` rather than requested as ASCII art.
    """

    title: str = Field(
        description=(
            "What the thing is called, naming what it does. Not 'Practice Project 1' and "
            "not a to-do list or a calculator unless the skill is genuinely about those."
        )
    )
    summary: str = Field(
        description=(
            "One or two sentences a learner could put on a CV: what it does and what it "
            "proves they can do."
        )
    )
    features: list[str] = Field(
        description=(
            "Three to six things the finished project does, each observable by using it. "
            "Every one must be buildable with what the course teaches."
        )
    )
    files: list[str] = Field(
        description=(
            "Every file and folder in the project as a full path from the project root, "
            "e.g. 'src/api/routes.py'. End folder paths with '/'. List them plainly, one "
            "per entry — do not draw a tree."
        )
    )
    milestones: list[str] = Field(
        description=(
            "Three to six steps in build order, each ending with something that runs. "
            "Never 'set up the project' as a milestone on its own."
        )
    )
    stretch_goals: list[str] = Field(
        description=(
            "Two to four extensions for a learner who finishes early, beyond what the "
            "course covered."
        )
    )


class ProjectPlan(BaseModel):
    """Response schema for project-agent.

    All the projects come from one call because they have to work as a ramp — separate calls
    would each reach for the most obvious idea and produce three variations of it.
    """

    projects: list[ProjectDraft] = Field(
        description=(
            "Projects in increasing order of ambition, each a different kind of thing rather "
            "than the previous one with more features."
        )
    )


class Project(BaseModel):
    """A portfolio project. `level` is how hard the project is within this course, not a
    claim about the learner — that is `LearningRequest.experience`."""

    level: ExperienceLevel
    title: str
    summary: str = ""
    features: list[str] = Field(default_factory=list)
    folder_structure: str = ""
    milestones: list[str] = Field(default_factory=list)
    stretch_goals: list[str] = Field(default_factory=list)


class QuizDraft(BaseModel):
    """Response shape for one quiz question.

    The right answer is asked for as text, never as an index. An index is a claim about a
    list the model has to keep in its head while writing it, and that claim is often wrong;
    the text is a thing it already knows. We build the options and locate the answer.
    """

    question: str = Field(
        description=(
            "One question with a single defensible answer. Ask about what the chapter "
            "taught, not about the chapter itself, and never say 'according to the text'."
        )
    )
    correct_answer: str = Field(
        description="The right answer, stated in full so it reads correctly on its own."
    )
    distractors: list[str] = Field(
        description=(
            "Three answers that are wrong but tempting: real mistakes a learner makes here. "
            "Each must be the same kind of thing and a similar length as the right answer, "
            "and none may be arguably correct."
        )
    )
    explanation: str = Field(
        description=(
            "One or two sentences: why the right answer is right and why a learner picking "
            "a wrong one went astray. Never mention option letters or positions."
        )
    )


class QuizSet(BaseModel):
    """Response schema for quiz-agent: the questions for one scope."""

    questions: list[QuizDraft] = Field(
        description="Questions in the order the material was taught, each testing a different point."
    )


class QuizQuestion(BaseModel):
    """An assembled question. `correct_index` is computed when the options are shuffled,
    so it cannot disagree with `options`."""

    question: str
    options: list[str]
    correct_index: int
    explanation: str = ""


class Quiz(BaseModel):
    """Machine-marked assessment. Anything that needs a human to judge it is a PracticeItem."""

    scope: str
    questions: list[QuizQuestion]
    # None marks the final assessment. Carried as a field so nothing has to parse `scope`.
    chapter_number: int | None = None


class ChapterVerdict(BaseModel):
    """Response shape for reviewing one chapter.

    The chapter number is not asked for — we know which chapter we sent — and neither is
    whether to rewrite it, which is a cost decision rather than a reading one.
    """

    score: int = Field(
        description=(
            "0-100 for this chapter alone. 90 or above means a learner could work through "
            "it unaided. Judge what is actually on the page, not how important the topic is."
        )
    )
    issues: list[str] = Field(
        default_factory=list,
        description=(
            "What a rewrite would have to fix, most serious first, each naming the "
            "specific passage or the missing explanation. Leave this empty when the "
            "chapter is sound — do not invent criticism to appear thorough."
        ),
    )
    unsupported_claims: list[str] = Field(
        default_factory=list,
        description=(
            "Every statement the chapter makes about the subject that the sources do not "
            "show: an import path, class, method, option, limit or behaviour that appears "
            "in no source. Quote the claim. Teaching commentary, analogies and general "
            "explanation are not claims about the subject and belong nowhere near this list."
        ),
    )


class CourseVerdict(BaseModel):
    """Response shape for the whole-course pass: the problems no single chapter can show."""

    issues: list[str] = Field(
        default_factory=list,
        description=(
            "Problems only visible across chapters: a prerequisite that is never taught, "
            "two chapters covering the same ground, or an order that uses something "
            "before explaining it. Do not repeat faults contained in a single chapter. "
            "Leave empty if the course holds together."
        ),
    )


class ReviewResult(BaseModel):
    score: int
    issues: list[str] = Field(default_factory=list)
    regenerate_chapters: list[int] = Field(default_factory=list)

    # Keyed by chapter number, so a rewrite can be told what was wrong with its last draft.
    chapter_issues: dict[int, list[str]] = Field(default_factory=dict)


class PublishedCourse(BaseModel):
    markdown_url: str
    pdf_url: str | None = None
    docx_url: str | None = None


class Rejection(BaseModel):
    """Terminal result when the prompt was not a request to learn something."""

    message: str


class Clarification(BaseModel):
    """Terminal result when the learner named several skills and chose none of them.

    Kept apart from Rejection: we understood the message and can help, we just must not
    guess which course they wanted.
    """

    message: str
    options: list[str]


class SubjectConfirmation(BaseModel):
    """Terminal result when the subject is identified but the learner has not seen it yet.

    Ranking skew is invisible to every check we can run: a search for a name one vendor
    dominates returns documents that genuinely all describe one subject, so the identity comes
    back `confirmed` and the invariant passes. Showing the learner the name and the pages it
    was read from costs one round trip; being wrong costs the whole expensive half of the run.
    """

    message: str
    canonical_name: str
    description: str
    source_urls: list[str]


class CourseState(BaseModel):
    """Passed between executors for the lifetime of one generation job."""

    job_id: str
    user_id: str
    prompt: str

    request: LearningRequest | None = None
    subject: SubjectAnalysis | None = None
    # The retrieved page text itself, so later nodes read evidence instead of re-fetching links.
    sources: list[SourceDocument] = Field(default_factory=list)
    subject_trace: SubjectTrace = Field(default_factory=SubjectTrace)
    # Set when the learner has already approved this subject, which is what lets a second run
    # skip straight past the nodes that produced it.
    subject_confirmed: bool = False
    research: list[ResearchSource] = Field(default_factory=list)
    curriculum: Curriculum | None = None
    chapters: list[Chapter] = Field(default_factory=list)
    practice: list[PracticeItem] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    quizzes: list[Quiz] = Field(default_factory=list)
    review: ReviewResult | None = None
    published: PublishedCourse | None = None

    completed_steps: list[WorkflowStep] = Field(default_factory=list)
    revision_count: int = 0

    def mark(self, step: WorkflowStep) -> None:
        """Record a finished step. The review loop revisits steps, so entries stay unique."""
        if step not in self.completed_steps:
            self.completed_steps.append(step)

    @property
    def should_regenerate(self) -> bool:
        """Keyed off the chapter list rather than the score, so the decision to loop and the
        work that loop would do cannot disagree."""
        return (
            self.review is not None
            and bool(self.review.regenerate_chapters)
            and self.revision_count < MAX_REVISIONS
        )

    @property
    def percent(self) -> int:
        return progress_percent(self.completed_steps)
