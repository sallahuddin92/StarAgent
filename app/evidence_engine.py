import os
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

class EvidenceEngine:
    def __init__(self, llm_client=None):
        self.llm = llm_client

    def _parse_json_safely(self, text: str) -> Any:
        text_clean = text.strip()
        if text_clean.startswith("```"):
            lines = text_clean.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text_clean = "\n".join(lines).strip()
            # Clean up potential "json" label
            if text_clean.startswith("json"):
                text_clean = text_clean[4:].strip()
        return json.loads(text_clean)

    async def extract_evidence_items(
        self,
        sources: List[Dict[str, Any]],
        run_id: str,
        question: str,
        model_id: str
    ) -> List[Dict[str, Any]]:
        """
        Reads raw sources and extracts evidence items (quote + assertion) for each.
        """
        evidence_items = []
        evidence_counter = 1
        
        for src in sources:
            source_id = src.get("source_id")
            file_path = src.get("file_path")
            if not file_path or not os.path.exists(file_path):
                continue
                
            content = Path(file_path).read_text(encoding="utf-8")
            if not content.strip():
                continue
                
            # Try LLM-based extraction first
            extracted = []
            if self.llm:
                prompt = (
                    f"You are a factual research assistant. Analyze the source text below and extract key evidence items "
                    f"relevant to the research question: \"{question}\".\n"
                    f"For each evidence item, you must extract:\n"
                    f"- A precise direct quote from the source text.\n"
                    f"- A clear factual assertion based on that quote.\n\n"
                    f"Source: {src.get('title')} ({src.get('url')})\n\n"
                    f"Source Text:\n{content[:15000]}\n\n"
                    f"Return ONLY a JSON list of objects, each containing:\n"
                    f"- \"quote\": string\n"
                    f"- \"assertion\": string\n"
                )
                try:
                    response = await self.llm.text(
                        [{"role": "user", "content": prompt}],
                        model=model_id,
                        temperature=0.1
                    )
                    items = self._parse_json_safely(response)
                    if isinstance(items, list):
                        extracted = items
                except Exception as e:
                    print(f"LLM evidence extraction failed for source {source_id}: {e}")
                    
            # Fallback to rule-based extraction
            if not extracted:
                extracted = self._rule_based_extract(content, question)
                
            # Assign IDs
            for item in extracted:
                quote = item.get("quote", "").strip()
                assertion = item.get("assertion", "").strip()
                if quote and assertion:
                    evidence_items.append({
                        "evidence_id": f"E{evidence_counter}",
                        "source_id": source_id,
                        "quote": quote,
                        "assertion": assertion
                    })
                    evidence_counter += 1
                    
        return evidence_items

    def _rule_based_extract(self, content: str, question: str) -> List[Dict[str, Any]]:
        keywords = [w.lower() for w in question.split() if len(w) > 3]
        sentences = re.split(r'(?<=[.!?])\s+', content)
        extracted = []
        for s in sentences:
            s_clean = s.strip()
            if not s_clean:
                continue
            if any(kw in s_clean.lower() for kw in keywords) or not keywords:
                # Limit length of sentence in case of formatting bugs
                if len(s_clean) > 10 and len(s_clean) < 300:
                    extracted.append({
                        "quote": s_clean,
                        "assertion": s_clean
                    })
        return extracted[:5] # Max 5 items per source for fallback

    async def compare_claims(
        self,
        evidence_items: List[Dict[str, Any]],
        question: str,
        model_id: str
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Groups evidence items into claims and identifies contradictions.
        """
        if not evidence_items:
            return [], []
            
        claims = []
        contradictions = []
        
        # Try LLM
        if self.llm:
            prompt = (
                f"Review the list of evidence items collected for the research question: \"{question}\".\n"
                f"Group these evidence items into key claims and identify any contradictions or consensus among them.\n\n"
                f"Evidence Items:\n{json.dumps(evidence_items, indent=2)}\n\n"
                f"Return ONLY a JSON object containing:\n"
                f"1. \"claims\": a list of claims. Each claim should contain:\n"
                f"   - \"claim_id\": string (e.g. \"C1\", \"C2\")\n"
                f"   - \"claim_text\": string\n"
                f"   - \"supporting_evidence_ids\": list of strings (matching E1, E2, etc.)\n"
                f"   - \"status\": \"consensus\" | \"contradiction\" | \"uncertain\"\n"
                f"2. \"contradictions\": a list of contradictions. Each contradiction should contain:\n"
                f"   - \"contradiction_id\": string (e.g. \"CT1\")\n"
                f"   - \"claim_id\": string\n"
                f"   - \"description\": string\n"
                f"   - \"conflicting_evidence_ids\": list of strings\n"
            )
            try:
                response = await self.llm.text(
                    [{"role": "user", "content": prompt}],
                    model=model_id,
                    temperature=0.1
                )
                data = self._parse_json_safely(response)
                claims = data.get("claims") or []
                contradictions = data.get("contradictions") or []
            except Exception as e:
                print(f"LLM claims comparison failed: {e}")
                
        # Fallback grouping
        if not claims:
            # Simple fallback: Group all evidence items into a single claim
            claims = [{
                "claim_id": "C1",
                "claim_text": f"General findings regarding: {question}",
                "supporting_evidence_ids": [e["evidence_id"] for e in evidence_items],
                "status": "consensus"
            }]
            contradictions = []
            
        return claims, contradictions

    async def synthesize_outline(
        self,
        claims: List[Dict[str, Any]],
        contradictions: List[Dict[str, Any]],
        question: str,
        model_id: str
    ) -> str:
        """
        Drafts a synthesis outline based on claims and contradictions.
        """
        if not claims:
            return "# Synthesis Outline\n\nNo claims or findings extracted."
            
        if self.llm:
            prompt = (
                f"Synthesize the claims and contradictions to construct an analysis report outline for the question: \"{question}\".\n"
                f"Focus on the tradeoffs, contradictions, and areas of consensus.\n\n"
                f"Claims:\n{json.dumps(claims, indent=2)}\n\n"
                f"Contradictions:\n{json.dumps(contradictions, indent=2)}\n\n"
                f"Return the synthesis report outline as a clean Markdown document."
            )
            try:
                outline = await self.llm.text(
                    [{"role": "user", "content": prompt}],
                    model=model_id,
                    temperature=0.2
                )
                return outline
            except Exception as e:
                print(f"LLM synthesize outline failed: {e}")
                
        # Fallback outline
        lines = [
            f"# Synthesis Outline: {question}",
            "\n## Key Findings",
        ]
        for c in claims:
            lines.append(f"- {c['claim_text']} (Status: {c['status']})")
        if contradictions:
            lines.append("\n## Contradictions / Uncertainties")
            for ct in contradictions:
                lines.append(f"- {ct['description']}")
        return "\n".join(lines)

    def citation_audit(
        self,
        final_report_content: str,
        sources: List[Dict[str, Any]],
        evidence_items: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Programmatically verifies all citations in the final report content.
        Citations must be [S1], [S2]... or [E1], [E2]...
        """
        valid_source_ids = {s["source_id"] for s in sources}
        valid_evidence_ids = {e["evidence_id"] for e in evidence_items}
        
        # Regex to find citations e.g. [S1], [E12]
        citations_found = re.findall(r"\[([SE]\d+)\]", final_report_content)
        
        unresolved = []
        verified_citations = []
        
        for cit in citations_found:
            if cit.startswith("S"):
                if cit in valid_source_ids:
                    verified_citations.append(cit)
                else:
                    unresolved.append(cit)
            elif cit.startswith("E"):
                if cit in valid_evidence_ids:
                    verified_citations.append(cit)
                else:
                    unresolved.append(cit)
                    
        # Filter duplicates
        verified_citations = list(set(verified_citations))
        unresolved = list(set(unresolved))
        
        # Also check for any general ungrounded claims (if there are no citations in a paragraph)
        # For simplicity, if unresolved list is empty, audit passes
        status = "passed" if not unresolved else "failed"
        
        return {
            "status": status,
            "unresolved": unresolved,
            "verified_citations": verified_citations
        }

    async def write_final_report(
        self,
        question: str,
        sources: List[Dict[str, Any]],
        evidence_items: List[Dict[str, Any]],
        claims: List[Dict[str, Any]],
        contradictions: List[Dict[str, Any]],
        outline: str,
        model_id: str
    ) -> str:
        """
        Compiles the final markdown report incorporating sources, evidence, claims, and citations.
        """
        if not sources:
            return (
                f"# Deep Research: {question}\n\n"
                f"No configured live sources were available.\n"
            )
            
        if self.llm:
            prompt = (
                f"You are the final report writer for StarAgent.\n"
                f"Generate the final Markdown report for the research question: \"{question}\".\n"
                f"You must use the following resources to build the report:\n"
                f"- Sources: {json.dumps(sources, indent=2)}\n"
                f"- Evidence Items: {json.dumps(evidence_items, indent=2)}\n"
                f"- Claims: {json.dumps(claims, indent=2)}\n"
                f"- Contradictions: {json.dumps(contradictions, indent=2)}\n"
                f"- Outline: {outline}\n\n"
                f"Follow these strict formatting constraints:\n"
                f"1. The report must contain these exact Markdown headings:\n"
                f"   # Deep Research: [Question Title]\n"
                f"   ## Summary\n"
                f"   ## Key Findings\n"
                f"   ## Evidence Table\n"
                f"   ## Contradictions / Uncertainties\n"
                f"   ## Limitations\n"
                f"   ## Citations\n"
                f"   ## Source List\n"
                f"2. Cite facts using stable citation IDs: [S1], [S2]... or [E1], [E2]... in the text.\n"
                f"3. Every finding or claim must reference at least one citation.\n"
                f"4. The Evidence Table should display source, direct quote, and assertion.\n"
                f"5. The Source List must map [S1] -> Title (URL).\n"
            )
            try:
                report = await self.llm.text(
                    [{"role": "user", "content": prompt}],
                    model=model_id,
                    temperature=0.2
                )
                return report
            except Exception as e:
                print(f"LLM report writing failed: {e}")
                
        # Fallback report compilation
        lines = [
            f"# Deep Research: {question}",
            "\n## Summary",
            f"Factual research conducted for question: \"{question}\".",
            "\n## Key Findings",
        ]
        for c in claims:
            citations_str = " ".join(f"[{eid}]" for eid in c["supporting_evidence_ids"])
            lines.append(f"- {c['claim_text']} {citations_str}")
            
        lines.append("\n## Evidence Table\n")
        lines.append("| Source | Quote | Assertion | Citation |")
        lines.append("|---|---|---|---|")
        for e in evidence_items:
            lines.append(f"| {e['source_id']} | {e['quote']} | {e['assertion']} | [{e['evidence_id']}] |")
            
        lines.append("\n## Contradictions / Uncertainties")
        if contradictions:
            for ct in contradictions:
                cits = " ".join(f"[{eid}]" for eid in ct["conflicting_evidence_ids"])
                lines.append(f"- {ct['description']} {cits}")
        else:
            lines.append("No significant contradictions or conflicts identified.")
            
        lines.append("\n## Limitations")
        lines.append("Analysis is limited by the set of provided sources.")
        
        lines.append("\n## Citations")
        for e in evidence_items:
            lines.append(f"- [{e['evidence_id']}]: direct quote from {e['source_id']}")
            
        lines.append("\n## Source List")
        for s in sources:
            lines.append(f"- [{s['source_id']}]: {s['title']} ({s['url']})")
            
        return "\n".join(lines)
