import os
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

class EvidenceEngine:
    def __init__(self, llm_client=None):
        self.llm = llm_client

    @staticmethod
    def _detect_version_in_question(question: str) -> Dict[str, Any]:
        """Detect version references and comparison patterns in a question.
        Returns dict with versions list, is_comparison flag, and comparison_versions."""
        versions = []
        # Match patterns like v0.6.1, v0.6.0, version 1.2.3
        for m in re.finditer(r'(?:v|version\s+)(\d+\.\d+(?:\.\d+)?)', question, re.IGNORECASE):
            v = m.group(0).strip().lower()
            # Normalize: "version 0.6.1" -> "v0.6.1"
            if v.startswith("version "):
                v = "v" + v[8:]
            if v not in versions:
                versions.append(v)

        is_comparison = bool(re.search(r'(vs?\.?|compared?\s+(?:to|with)|and|versus)\s', question, re.IGNORECASE)) and len(versions) >= 2
        return {
            "versions": versions,
            "is_comparison": is_comparison,
            "comparison_versions": versions if is_comparison else []
        }

    def _score_evidence_relevance(
        self,
        question: str,
        source_title: str,
        content_lines: List[str],
        evidence_quote: str,
        version_info: Dict[str, Any]
    ) -> Tuple[float, str]:
        """Score an evidence item for relevance to the research question.
        Returns (score 0.0-1.0, reason string)."""
        versions = version_info.get("versions", [])
        score = 0.3  # base score
        reasons = []

        quote_lower = evidence_quote.lower()

        # Find which version-specific section this evidence belongs to.
        evidence_section = ""
        # Use only the first line of the quote for matching against content lines
        first_line = evidence_quote.split("\n")[0].strip().lower()
        match_idx = -1
        for idx, line in enumerate(content_lines):
            stripped = line.strip().lower()
            if first_line in stripped or stripped in first_line:
                match_idx = idx
                break
        if match_idx >= 0:
            # Scan backward for nearest version-bearing heading (H1/H2)
            for j in range(match_idx, -1, -1):
                hm = re.match(r'^(#{1,2})\s+(.*)', content_lines[j])
                if hm:
                    hv = re.search(r'(?:v|version\s+)(\d+\.\d+(?:\.\d+)?)', content_lines[j], re.IGNORECASE)
                    if hv:
                        evidence_section = content_lines[j].lower()
                        break
                    # If no version in this heading, check if it's a section sub-heading
                    # Continue scanning for the containing H1
                    if hm.group(1) == "##":
                        continue  # Skip sub-headings, look for parent H1
                    evidence_section = content_lines[j].lower()
                    break

        section_lower = evidence_section.lower()

        version_match = False
        unrelated_version = None
        for v in versions:
            vn = v.replace("v", "")  # "v0.6.1" -> "0.6.1"
            if vn in quote_lower or vn in section_lower or v in quote_lower:
                score += 0.5
                reasons.append(f"matches version {v}")
                version_match = True
                break

        # Check if the evidence's own section heading contains an unrelated version
        if not version_match and evidence_section:
            hm = re.match(r'^#{1,4}\s+(.*)', evidence_section)
            if hm:
                heading = hm.group(1).lower()
                hv = re.search(r'(?:v|version\s+)?(\d+\.\d+(?:\.\d+)?)', heading)
                if hv:
                    hv_full = "v" + hv.group(1)
                    if hv_full not in versions:
                        unrelated_version = hv_full

        if unrelated_version:
            score -= 0.4
            reasons.append(f"penalty: from unrelated version {unrelated_version}")

        # Keyword overlap
        keywords = [w.lower() for w in question.split() if len(w) > 3]
        if any(kw in quote_lower for kw in keywords):
            score += 0.2
            reasons.append("keyword match")

        # Source title match
        if source_title and any(kw in source_title.lower() for kw in keywords):
            score += 0.1
            reasons.append("source title match")

        # Clamp to [0.0, 1.0]
        score = max(0.0, min(1.0, score))
        reason = "; ".join(reasons) if reasons else "default"
        return (score, reason)

    @staticmethod
    def _build_limitation_report(question: str, reason: str) -> str:
        """Build a standardized limitation report when evidence is insufficient."""
        return (
            f"# Deep Research: {question}\n\n"
            f"## Summary\n"
            f"Research could not be completed due to insufficient evidence.\n\n"
            f"## Limitations\n"
            f"{reason}\n\n"
            f"---\n"
            f"**Status**: completed_with_limitations\n"
            f"**Reason**: {reason} No citations were fabricated.\n"
        )

    @staticmethod
    def _build_comparison_report(
        question: str,
        accepted_evidence: List[Dict[str, Any]],
        sources: List[Dict[str, Any]]
    ) -> str:
        """Build a version comparison report from accepted evidence
        with version-grouped sections, confirmed improvements, and limitations."""
        versions = []
        for m in re.finditer(r'(?:v|version\s+)?(\d+\.\d+(?:\.\d+)?)', question, re.IGNORECASE):
            v = "v" + m.group(1) if not m.group(0).lower().startswith("v") else m.group(0).lower().strip()
            if v not in versions:
                versions.append(v)

        v_groups = {v: [] for v in versions}
        for e in accepted_evidence:
            q = e["quote"].lower()
            for v in versions:
                vn = v.replace("v", "")
                if vn in q or v in q:
                    v_groups[v].append(e)
                    break

        lines = [
            f"# Deep Research: {question}",
            "\n## Summary",
            f"Version comparison research for: {question}",
        ]

        for v in versions:
            ev = v_groups.get(v, [])
            lines.append(f"\n## Evidence for {v}")
            if ev:
                for e in ev:
                    lines.append(f"- [{e['evidence_id']}] {e['assertion']}")
            else:
                lines.append(f"- No specific evidence found for {v}")

        lines.append("\n## Confirmed Improvements")
        confirmed = [e for e in accepted_evidence if any(k in e["quote"].lower() for k in
                      ["improve", "better", "new", "added", "fix", "enhance", "upgrade"])]
        if confirmed:
            for e in confirmed:
                lines.append(f"- [{e['evidence_id']}] {e['assertion']}")
        else:
            lines.append("- No specific improvement claims could be confirmed from available evidence.")

        lines.append("\n## Missing / Insufficient Evidence")
        missing = [v for v in versions if not v_groups.get(v)]
        if missing:
            for v in missing:
                lines.append(f"- No evidence found for {v}")
        lines.append("Analysis limited to available accepted evidence only.")

        lines.append("\n## Limitations")
        lines.append("Analysis is limited by available accepted evidence only.")

        lines.append("\n## Citations")
        for e in accepted_evidence:
            lines.append(f"- [{e['evidence_id']}]: direct quote from {e['source_id']}")

        lines.append("\n## Source List")
        for s in sources:
            lines.append(f"- [{s['source_id']}]: {s['title']} ({s['url']})")

        return "\n".join(lines)

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
        Each item is scored for relevance; items from unrelated version sections
        may be marked as accepted=False.
        """
        version_info = self._detect_version_in_question(question)
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

            content_lines = content.splitlines()

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

            # Assign IDs with relevance scoring
            for item in extracted:
                quote = item.get("quote", "").strip()
                assertion = item.get("assertion", "").strip()
                if quote and assertion:
                    score, reason = self._score_evidence_relevance(
                        question, src.get("title", ""), content_lines,
                        quote, version_info
                    )
                    accepted = score >= 0.3
                    evidence_item = {
                        "evidence_id": f"E{evidence_counter}",
                        "source_id": source_id,
                        "quote": quote,
                        "assertion": assertion,
                        "relevance_score": round(score, 2),
                        "relevance_reason": reason,
                        "accepted": accepted
                    }
                    evidence_items.append(evidence_item)
                    evidence_counter += 1

        return evidence_items

    def _rule_based_extract(self, content: str, question: str) -> List[Dict[str, Any]]:
        """Extract evidence from markdown content using rule-based parsing.

        Handles bullet lists, table rows, and paragraphs. Each yields a
        (quote, assertion) pair where the assertion is a concise factual
        claim derived from the quote.
        """
        lines = content.splitlines()
        extracted = []

        def make_assertion(text: str) -> str:
            """Derive a concise factual assertion from source text."""
            # Strip leading bullet markers, bold, and headings
            clean = re.sub(r'^[\s*\-]+\*{0,2}', '', text).strip()
            clean = re.sub(r'\*{2}', '', clean)
            # Take first sentence
            first_sent = re.split(r'(?<=[.!?])\s+', clean)[0].strip()
            # Strip trailing colon (from definition-list bullets)
            first_sent = first_sent.rstrip(':')
            # Limit length
            if len(first_sent) > 150:
                first_sent = first_sent[:147] + "..."
            return first_sent if len(first_sent) > 10 else text.strip()[:150]

        # 1. Extract bullet items
        for line in lines:
            stripped = line.strip()
            # Match lines starting with - or * (markdown bullets)
            bullet = re.match(r'^[\s]*[-*+]\s+(.*)', stripped)
            if bullet:
                item = bullet.group(1).strip()
                # Skip if it's just a heading-like bullet
                if len(item) > 10 and not item.startswith('#'):
                    assertion = make_assertion(item)
                    extracted.append({"quote": item, "assertion": assertion})

        # 2. Extract table rows (lines containing | with meaningful content)
        for line in lines:
            stripped = line.strip()
            if stripped.count('|') >= 2 and '---' not in stripped:
                cells = [c.strip() for c in stripped.split('|') if c.strip()]
                if len(cells) >= 2:
                    row_text = " | ".join(cells)
                    if len(row_text) > 15:
                        assertion = make_assertion(cells[0])
                        extracted.append({"quote": row_text, "assertion": assertion})

        # 3. Extract meaningful paragraphs (skip headings, short lines, bullets)
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Skip headings, bullets (already handled), table rows, short lines
            if stripped.startswith('#'):
                continue
            if re.match(r'^[\s]*[-*+]', stripped):
                continue
            if '|' in stripped:
                continue
            if len(stripped) < 20 or len(stripped) > 500:
                continue
            # Must be a non-empty paragraph
            assertion = make_assertion(stripped)
            extracted.append({"quote": stripped, "assertion": assertion})

        # Deduplicate by quote (exact match)
        seen = set()
        deduped = []
        for item in extracted:
            if item["quote"] not in seen:
                seen.add(item["quote"])
                deduped.append(item)

        return deduped[:10]  # Max 10 items per source for fallback

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
        Only accepted evidence items (relevance_score >= 0.3) are used.
        For version comparison questions, a structured comparison report is generated.
        """
        # Filter to accepted evidence only (backward compat: missing accepted field = True)
        accepted_evidence = [e for e in evidence_items if e.get("accepted", True)]

        if not sources:
            return (
                f"# Deep Research: {question}\n\n"
                f"No configured live sources were available.\n\n"
                f"---\n"
                f"**Status**: completed_with_limitations\n"
                f"**Reason**: No configured live sources were available. No citations were fabricated.\n"
            )

        # If no accepted evidence, return limitation report
        if sources and not accepted_evidence:
            return self._build_limitation_report(
                question,
                "All extracted evidence was rejected by relevance filtering — no usable evidence available for report."
            )

        # Detect version comparison for structured report format
        version_info = self._detect_version_in_question(question)
        if version_info["is_comparison"] and not self.llm:
            return self._build_comparison_report(question, accepted_evidence, sources)

        if self.llm:
            prompt = (
                f"You are the final report writer for StarAgent.\n"
                f"Generate the final Markdown report for the research question: \"{question}\".\n"
                f"You must use the following resources to build the report:\n"
                f"- Sources: {json.dumps(sources, indent=2)}\n"
                f"- Evidence Items: {json.dumps(accepted_evidence, indent=2)}\n"
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
                f"4. The Evidence Table should display evidence_id, source_id, relevance_score, quote, and assertion.\n"
                f"5. The Source List must map [S1] -> Title (URL).\n"
                f"6. IMPORTANT: You may ONLY cite accepted evidence items (list above). Do not cite any other evidence.\n"
                f"   The accepted evidence list is: {[e['evidence_id'] for e in accepted_evidence]}\n"
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

        # Fallback report compilation (using only accepted evidence)
        lines = [
            f"# Deep Research: {question}",
            "\n## Summary",
            f"Factual research conducted for question: \"{question}\".",
            "\n## Key Findings",
        ]
        # Filter claims to only reference accepted evidence
        accepted_ids = {e["evidence_id"] for e in accepted_evidence}
        for c in claims:
            filtered_eids = [eid for eid in c.get("supporting_evidence_ids", []) if eid in accepted_ids]
            if filtered_eids:
                citations_str = " ".join(f"[{eid}]" for eid in filtered_eids)
                lines.append(f"- {c['claim_text']} {citations_str}")

        lines.append("\n## Evidence Table\n")
        lines.append("| Evidence ID | Source | Relevance | Quote | Assertion |")
        lines.append("|---|---|---|---|---|")
        for e in accepted_evidence:
            score = e.get("relevance_score", "?")
            lines.append(f"| [{e['evidence_id']}] | {e['source_id']} | {score} | {e['quote']} | {e['assertion']} |")

        lines.append("\n## Contradictions / Uncertainties")
        if contradictions:
            for ct in contradictions:
                cits = " ".join(f"[{eid}]" for eid in ct.get("conflicting_evidence_ids", []) if eid in accepted_ids)
                if cits:
                    lines.append(f"- {ct['description']} {cits}")
        else:
            lines.append("No significant contradictions or conflicts identified.")

        lines.append("\n## Limitations")
        if accepted_evidence:
            lines.append("Analysis is limited by the set of provided sources.")
        else:
            lines.append("No accepted evidence was available for report generation.")

        lines.append("\n## Citations")
        for e in accepted_evidence:
            lines.append(f"- [{e['evidence_id']}]: direct quote from {e['source_id']}")

        lines.append("\n## Source List")
        for s in sources:
            lines.append(f"- [{s['source_id']}]: {s['title']} ({s['url']})")

        return "\n".join(lines)
