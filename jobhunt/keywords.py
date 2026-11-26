"""Tech-keyword extraction shared by tailoring, the fact guard and job scoring.

One vocabulary maps the many spellings in job posts ("Node", "NodeJS", "node.js") to a
canonical term. The same extractor runs over the profile, so "what the profile supports"
and "what the posting asks for" are compared in the same units.
"""

from __future__ import annotations

import re

# canonical term -> regex alternatives (matched case-insensitively, on word boundaries).
_TERMS: dict[str, list[str]] = {
    # languages
    "TypeScript": [r"typescript"],
    "JavaScript": [r"javascript", r"es6"],
    "Python": [r"python"],
    "Java": [r"java(?!\s*script)"],
    "Kotlin": [r"kotlin"],
    "Swift": [r"swift(?:ui)?"],
    "Go": [r"golang"],
    "Rust": [r"rust"],
    "C++": [r"c\+\+", r"cpp"],
    "C#": [r"c#"],
    ".NET": [r"\.net", r"dotnet"],
    "PHP": [r"php"],
    "Ruby": [r"ruby", r"rails"],
    "Scala": [r"scala"],
    "SQL": [r"sql"],
    "Dart": [r"dart"],
    "HTML": [r"html5?"],
    "CSS": [r"css3?"],
    # backend
    "Node.js": [r"node\.?js", r"node"],
    "NestJS": [r"nest\.?js"],
    "Express.js": [r"express\.?js"],
    "FastAPI": [r"fast\s?api"],
    "Django": [r"django"],
    "Flask": [r"flask"],
    "Spring Boot": [r"spring\s?boot", r"spring"],
    "GraphQL": [r"graphql"],
    "REST APIs": [r"rest(?:ful)?(?:\s+apis?)?", r"apis?"],
    "gRPC": [r"grpc"],
    "Microservices": [r"micro-?services?"],
    "WebSockets": [r"web\s?-?sockets?", r"socket\.io"],
    "Redis": [r"redis"],
    "BullMQ": [r"bullmq", r"bull\s?queue"],
    "Kafka": [r"kafka"],
    "RabbitMQ": [r"rabbitmq"],
    "Message Queues": [r"message\s+queues?", r"queues?"],
    "Authentication": [r"authentication", r"oauth\s?2?(?:\.0)?", r"jwt", r"auth"],
    "Payments": [r"payments?", r"payment\s+gateway"],
    # data
    "PostgreSQL": [r"postgres(?:ql)?", r"psql"],
    "MySQL": [r"mysql"],
    "MongoDB": [r"mongo(?:db)?"],
    "Supabase": [r"supabase"],
    "Firebase": [r"firebase"],
    "DynamoDB": [r"dynamo\s?db"],
    "Elasticsearch": [r"elastic\s?search", r"opensearch"],
    "Full-text Search": [r"full[- ]text\s+search", r"trigram"],
    "Prisma": [r"prisma"],
    "TypeORM": [r"typeorm"],
    "Sequelize": [r"sequelize"],
    "Data Pipelines": [r"etl", r"elt", r"data\s+pipelines?"],
    # frontend / mobile
    "React": [r"react(?:\.?js)?(?![\s-]*native)"],
    "Next.js": [r"next\.?js"],
    "React Native": [r"react[\s-]*native"],
    "Flutter": [r"flutter"],
    "Redux": [r"redux(?:\s+toolkit)?"],
    "Angular": [r"angular(?:js)?"],
    "Vue": [r"vue(?:\.?js)?"],
    "Tailwind CSS": [r"tailwind(?:\s?css)?"],
    "Android": [r"android"],
    "iOS": [r"ios"],
    "Expo": [r"expo"],
    # cloud / devops
    "AWS": [r"aws", r"amazon\s+web\s+services"],
    "AWS Lambda": [r"lambda"],
    "S3": [r"s3"],
    "EC2": [r"ec2"],
    "Athena": [r"athena"],
    "SQS": [r"sqs"],
    "GCP": [r"gcp", r"google\s+cloud"],
    "Cloud Run": [r"cloud\s+run"],
    "Azure": [r"azure"],
    "Docker": [r"docker", r"containeri[sz]ation", r"containers?"],
    "Kubernetes": [r"kubernetes", r"k8s"],
    "Terraform": [r"terraform"],
    "CI/CD": [r"ci\s?/\s?cd", r"continuous\s+(?:integration|delivery|deployment)", r"github\s+actions", r"jenkins"],
    "Linux": [r"linux", r"unix"],
    "Nginx": [r"nginx"],
    "Vercel": [r"vercel"],
    "Serverless": [r"serverless"],
    # AI
    "LLM": [r"llms?", r"large\s+language\s+models?", r"llm\s+integration"],
    "Generative AI": [r"gen\s?ai", r"generative\s+ai"],
    "RAG": [r"rag", r"retrieval[- ]augmented(?:\s+generation)?"],
    "LangChain": [r"lang\s?chain", r"lang\s?graph"],
    "LlamaIndex": [r"llama\s?index"],
    "OpenAI": [r"openai", r"gpt-?\d*"],
    "Claude": [r"claude(?:\s+(?:api|code|haiku|sonnet|opus))?", r"anthropic"],
    "Vector Databases": [r"vector\s+(?:db|database|store|search)s?", r"pinecone", r"weaviate", r"qdrant", r"milvus", r"chroma(?:db)?", r"pgvector"],
    "Embeddings": [r"embeddings?"],
    "Prompt Engineering": [r"prompt\s+engineering", r"prompting", r"prompt\s+caching"],
    "AI Agents": [r"ai\s+agents?", r"agentic", r"multi-?agent", r"agent\s+orchestration"],
    "MCP": [r"mcp", r"model\s+context\s+protocol"],
    "Tool Calling": [r"tool[- ]calling", r"function[- ]calling", r"tool\s+use"],
    "Structured Outputs": [r"structured\s+outputs?"],
    "LLM Evals": [r"llm\s+evals?", r"evals"],
    "Machine Learning": [r"machine\s+learning", r"ml"],
    "NLP": [r"nlp", r"natural\s+language\s+processing"],
    "PyTorch": [r"pytorch"],
    "TensorFlow": [r"tensorflow"],
    "Hugging Face": [r"hugging\s?face"],
    "Ollama": [r"ollama"],
    # testing and practice
    "Jest": [r"jest"],
    "Pytest": [r"pytest"],
    "Playwright": [r"playwright"],
    "Unit Testing": [r"unit\s+tests?", r"unit\s+testing", r"tdd"],
    "System Design": [r"system\s+design", r"distributed\s+systems?", r"scalab(?:le|ility)"],
    "Data Structures & Algorithms": [r"data\s+structures?", r"algorithms?", r"dsa"],
    "Git": [r"git", r"github", r"gitlab"],
    "Agile": [r"agile", r"scrum"],
    # domain words that show up in the profile and in posts
    "Multi-tenant SaaS": [r"multi-?tenant", r"saas"],
    "Real-time": [r"real[- ]time", r"live[- ]streaming"],
    "IoT": [r"iot", r"esp32", r"bluetooth", r"embedded"],
}

# Terms that are too generic to count as a "missing skill" when a post mentions them.
GENERIC = {"REST APIs", "Git", "Agile", "Data Structures & Algorithms", "System Design", "Message Queues",
           "Authentication", "Docker", "Real-time", "Unit Testing", "SQL"}

# "Go" only counts when it's clearly the language, not the verb.
_GO_RE = re.compile(r"(?<![A-Za-z])Go(?=\s*(?:[,/)]|\s+(?:developer|engineer|programming|language|lang)\b))")

_COMPILED = {
    term: re.compile(r"(?<![\w.+#])(?:" + "|".join(alts) + r")(?![\w+#])", re.I)
    for term, alts in _TERMS.items()
}

ALL_TERMS: tuple[str, ...] = tuple(_TERMS)


def extract_terms(text: str) -> set[str]:
    """Canonical tech terms mentioned in free text."""
    text = text or ""
    found = {term for term, rx in _COMPILED.items() if rx.search(text)}
    if _GO_RE.search(text):
        found.add("Go")
    # "AWS Lambda" / "S3" imply AWS; "Cloud Run" implies GCP.
    if found & {"AWS Lambda", "S3", "EC2", "Athena", "SQS"}:
        found.add("AWS")
    if "Cloud Run" in found:
        found.add("GCP")
    return found


def first_positions(text: str) -> dict[str, int]:
    """Where each canonical term first appears in `text` (for ordering by what a post stresses first)."""
    text = text or ""
    pos = {}
    for term, rx in _COMPILED.items():
        if m := rx.search(text):
            pos[term] = m.start()
    if m := _GO_RE.search(text):
        pos["Go"] = m.start()
    return pos


# Not glued to a word, so ESP32 / S3 / EC2 aren't read as numbers.
_NUMBER_RE = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?\s*(?:%|x\b|\+|k\b|lpa)?", re.I)


def numbers_in(text: str) -> set[str]:
    """Numbers as written ("1,937", "$0.0985", "84%", "20x", "30+"), normalized for comparison."""
    return {re.sub(r"\s+", "", m.group(0)).lower().rstrip(".,") for m in _NUMBER_RE.finditer(text or "")}


_YEARS_RE = re.compile(
    r"(\d{1,2})(?:\.\d)?\s*(?:\+|plus)?\s*(?:(?:-|–|—|to)\s*(\d{1,2})\s*)?\+?\s*(?:years?|yrs?)\b",
    re.I,
)
_EXP_CONTEXT_RE = re.compile(r"experience|exp\b|background|working|hands[- ]on|professional", re.I)


def years_required(text: str) -> int | None:
    """Minimum years of experience a post asks for, or None if it doesn't say.

    Takes the largest lower bound found near the word "experience", so
    "3+ years of experience ... 1+ year with Docker" -> 3, and "2-4 years" -> 2.
    """
    text = text or ""
    lows: list[int] = []
    for m in _YEARS_RE.finditer(text):
        window = text[max(0, m.start() - 80): m.end() + 80]
        if not _EXP_CONTEXT_RE.search(window):
            continue
        low = int(m.group(1))
        if low <= 20:
            lows.append(low)
    return max(lows) if lows else None


def slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:limit].strip("-") or "x"
