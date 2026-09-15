import sys
from pathlib import Path
from resume_parser import StructuredResumeProfile, structure_resume_with_ai, format_candidate_context_for_prompt

sample_resume = """
John Doe
Email: john.doe.ai@gmail.com | Phone: +91 9876543210
LinkedIn: linkedin.com/in/johndoe-ai | GitHub: github.com/johndoe

Summary:
Full-stack AI/ML Engineer with 3+ years of experience designing, fine-tuning, and deploying Large Language Models (LLMs), RAG architectures, and scalable machine learning pipelines on AWS and GCP.

Key Skills:
Python, PyTorch, LangChain, LlamaIndex, FastAPI, Docker, Kubernetes, Vector DBs (ChromaDB, Pinecone), PostgreSQL, AWS SageMaker.

Projects & Experience:
1. Production RAG Agent Platform: Built an enterprise multi-agent search engine indexing 50,000+ internal research papers using LangChain and Qdrant, reducing search latency by 45%.
2. Real-Time Voice AI Streamer: Developed a low-latency WebRTC speech-to-text and AI voice agent pipeline serving 1,000 concurrent streams.
3. LLM Fine-tuning for Code Gen: Fine-tuned LLaMA-3 8B using LoRA / QLoRA for automated PR code reviews with 92% syntax validity.

Education:
B.Tech in Computer Science, IIT Bombay (2022)
"""

print("Testing Resume Parser with Groq AI + Pydantic BaseModel...")
try:
    profile: StructuredResumeProfile = structure_resume_with_ai(sample_resume)
    print("\n--- Pydantic Validation Success! ---")
    print(f"Name       : {profile.name}")
    print(f"Role       : {profile.headline_or_role}")
    print(f"Skills     : {profile.key_skills}")
    print(f"Projects   : {len(profile.key_projects_or_achievements)} extracted")
    print("\n--- Prompt Injection Context Output ---")
    print(format_candidate_context_for_prompt(profile))
    print("\nALL TESTS PASSED!")
except Exception as e:
    print(f"TEST FAILED: {e}")
    sys.exit(1)
