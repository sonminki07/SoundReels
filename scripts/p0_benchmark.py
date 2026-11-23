"""
SoundReels P0 Benchmark & Quality Validation Script
- Tests PyTorch CUDA availability and GTX 1060 (Pascal sm_61) compatibility
- Downloads curated CC0 sound effects from LotsOfSounds API
- Evaluates LAION-CLAP semantic text-to-audio search & audio-to-audio similarity
"""

import os
import sys
import time
import json
import urllib.request
from pathlib import Path

def test_hardware():
    print("=" * 60)
    print("1. Hardware & Environment Check")
    print("=" * 60)
    print(f"Python executable: {sys.executable}")
    print(f"Python version: {sys.version.split()[0]}")
    
    import torch
    print(f"PyTorch version: {torch.__version__}")
    cuda_available = torch.cuda.is_available()
    print(f"CUDA Available: {cuda_available}")
    
    device = "cpu"
    if cuda_available:
        device_name = torch.cuda.get_device_name(0)
        vram_mb = torch.cuda.get_device_properties(0).total_memory / (1024 * 1024)
        cap = torch.cuda.get_device_capability(0)
        print(f"GPU: {device_name} (Capability: {cap[0]}.{cap[1]}, VRAM: {vram_mb:.0f} MB)")
        try:
            # Simple CUDA tensor test to verify sm_61 kernel execution
            x = torch.randn(1000, 1000, device="cuda")
            y = torch.matmul(x, x)
            torch.cuda.synchronize()
            print("CUDA Tensor MatMul Test: PASSED (Kernel executed on GPU)")
            device = "cuda"
        except Exception as e:
            print(f"CUDA Execution Failed: {e}. Falling back to CPU.")
            device = "cpu"
    else:
        print("Running on CPU.")
    
    return device

def fetch_samples_from_lotsofsounds(target_dir: Path):
    print("\n" + "=" * 60)
    print("2. Fetching CC0 Sound Samples (LotsOfSounds API)")
    print("=" * 60)
    target_dir.mkdir(parents=True, exist_ok=True)
    
    queries = [
        "door knock",
        "metal slam",
        "laser",
        "coin",
        "footstep",
        "explosion",
        "sword",
        "water splash",
        "wood break",
        "bell chime",
        "ui click"
    ]
    
    collected_files = []
    
    for q in queries:
        try:
            encoded_q = urllib.parse.quote(q)
            url = f"https://api.lotsofsounds.com/api/v1/sounds/sample?q={encoded_q}&limit=4"
            req = urllib.request.Request(url, headers={"User-Agent": "SoundReels-P0-Benchmark/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("data", [])
                print(f"Query '{q}': found {len(items)} samples")
                
                for item in items:
                    sound_id = item.get("id")
                    name = item.get("name", "sound").replace(" ", "_").replace("/", "_")
                    stream_path = item.get("stream_url")
                    if not stream_path:
                        continue
                    
                    if stream_path.startswith("/"):
                        stream_url = f"https://api.lotsofsounds.com{stream_path}"
                    else:
                        stream_url = stream_path
                    
                    ext = ".mp3"
                    file_name = f"{sound_id}_{name}{ext}"
                    local_path = target_dir / file_name
                    
                    if not local_path.exists() or local_path.stat().st_size < 1000:
                        try:
                            # 1. Call stream endpoint to get the real signed audio URL
                            stream_meta_req = urllib.request.Request(stream_url, headers={"User-Agent": "SoundReels-P0-Benchmark/1.0"})
                            with urllib.request.urlopen(stream_meta_req, timeout=10) as s_resp:
                                stream_meta = json.loads(s_resp.read().decode("utf-8"))
                                actual_audio_url = stream_meta.get("data", {}).get("stream_url")
                                
                            if not actual_audio_url:
                                continue
                                
                            # 2. Download the actual audio stream
                            down_req = urllib.request.Request(actual_audio_url, headers={"User-Agent": "SoundReels-P0-Benchmark/1.0"})
                            with urllib.request.urlopen(down_req, timeout=15) as down_resp, open(local_path, "wb") as f:
                                f.write(down_resp.read())
                        except Exception as dl_err:
                            print(f"  Failed to download {sound_id}: {dl_err}")
                            continue
                    
                    collected_files.append({
                        "id": sound_id,
                        "name": item.get("name"),
                        "description": item.get("description", ""),
                        "tags": item.get("tags", []),
                        "duration": item.get("duration", 0),
                        "path": str(local_path)
                    })
        except Exception as e:
            print(f"Error fetching for '{q}': {e}")
            
    print(f"Total valid samples ready for benchmark: {len(collected_files)}")
    return collected_files

def benchmark_clap(samples, device="cpu"):
    print("\n" + "=" * 60)
    print("3. Benchmarking LAION-CLAP Embedding & Search")
    print("=" * 60)
    
    import torch
    import librosa
    from transformers import ClapModel, ClapProcessor
    
    model_id = "laion/clap-htsat-unfused"
    print(f"Loading CLAP model ({model_id}) on {device.upper()}...")
    t0 = time.time()
    processor = ClapProcessor.from_pretrained(model_id)
    model = ClapModel.from_pretrained(model_id, use_safetensors=True).to(device).eval()
    print(f"Model loaded in {time.time() - t0:.2f}s")
    
    print("\nExtracting audio embeddings for dataset...")
    embeddings = []
    valid_samples = []
    
    t_start_embed = time.time()
    for s in samples:
        try:
            # Standard CLAP 48kHz sampling rate
            audio, _ = librosa.load(s["path"], sr=48000, mono=True)
            if len(audio) == 0:
                continue
            
            # Truncate to first 10 seconds for consistent fast benchmarking
            max_len = 48000 * 10
            if len(audio) > max_len:
                audio = audio[:max_len]
                
            inputs = processor(audio=audio, sampling_rate=48000, return_tensors="pt").to(device)
            with torch.no_grad():
                emb = model.get_audio_features(**inputs)
                emb = emb / emb.norm(dim=-1, keepdim=True)
                embeddings.append(emb.cpu())
                valid_samples.append(s)
        except Exception as err:
            print(f"  Error embedding {s['path']}: {err}")
            
    print(f"Indexed {len(embeddings)} sounds in {time.time() - t_start_embed:.2f}s (avg: {(time.time() - t_start_embed)/max(len(embeddings), 1)*1000:.1f}ms/sound)")
    
    all_embeddings = torch.cat(embeddings, dim=0) # [N, D]
    
    # 4. Text-to-Audio Search Test with 10 Game SFX Queries
    test_queries = [
        {"desc": "Heavy wooden door closing shut", "category": "door"},
        {"desc": "Metal blade sword slash in combat", "category": "sword"},
        {"desc": "Loud bomb explosion blast with debris", "category": "explosion"},
        {"desc": "Futuristic sci-fi laser gun firing", "category": "laser"},
        {"desc": "Gold coin pickup blip sound", "category": "coin"},
        {"desc": "Player boots footstep on hard ground", "category": "footstep"},
        {"desc": "Water splash in pond or river", "category": "water"},
        {"desc": "Wood plank breaking and snapping", "category": "wood"},
        {"desc": "Magical fairy chime ringing gently", "category": "bell"},
        {"desc": "Clean crisp UI button click feedback", "category": "ui"}
    ]
    
    print("\n" + "=" * 60)
    print("4. Text Semantic Search Evaluation (Top-3)")
    print("=" * 60)
    
    top1_hits = 0
    top3_hits = 0
    
    for tq in test_queries:
        q_text = tq["desc"]
        category = tq["category"]
        
        inputs = processor(text=[q_text], return_tensors="pt", padding=True).to(device)
        with torch.no_grad():
            text_emb = model.get_text_features(**inputs)
            text_emb = text_emb / text_emb.norm(dim=-1, keepdim=True)
            
        scores = (all_embeddings @ text_emb.cpu().T).squeeze(-1)
        top_k = torch.topk(scores, k=min(3, len(scores)))
        
        print(f"\n[Query] \"{q_text}\" (Target: {category})")
        hit_in_top1 = False
        hit_in_top3 = False
        
        for rank, (score, idx) in enumerate(zip(top_k.values, top_k.indices), start=1):
            sound = valid_samples[idx]
            is_relevant = (category.lower() in sound["name"].lower() or 
                           category.lower() in sound.get("description", "").lower() or 
                           any(category.lower() in t.lower() for t in sound.get("tags", [])))
            rel_str = "MATCH" if is_relevant else "MISMATCH"
            print(f"  #{rank} [Score: {score.item():.3f}] {sound['name']} | {rel_str}")
            
            if is_relevant:
                hit_in_top3 = True
                if rank == 1:
                    hit_in_top1 = True
                    
        if hit_in_top1:
            top1_hits += 1
        if hit_in_top3:
            top3_hits += 1
            
    print("\n" + "=" * 60)
    print("5. Benchmark Summary & Precision Metrics")
    print("=" * 60)
    total_q = len(test_queries)
    print(f"Top-1 Accuracy: {top1_hits}/{total_q} ({top1_hits/total_q*100:.1f}%)")
    print(f"Top-3 Recall:   {top3_hits}/{total_q} ({top3_hits/total_q*100:.1f}%)")
    
    target_met = (top3_hits / total_q) >= 0.5
    print(f"\nP0 Verification Criteria (Top-3 Recall >= 50%): {'PASSED' if target_met else 'NEEDS_TUNING'}")
    
    # 6. Audio-to-Audio Similarity Test ("Similar Sounds" feature)
    print("\n" + "=" * 60)
    print("6. Audio-to-Audio Similarity Test ('Similar Sounds' Feature)")
    print("=" * 60)
    ref_idx = 0
    ref_sound = valid_samples[ref_idx]
    ref_emb = all_embeddings[ref_idx:ref_idx+1]
    
    sim_scores = (all_embeddings @ ref_emb.T).squeeze(-1)
    sim_top = torch.topk(sim_scores, k=min(4, len(sim_scores)))
    
    print(f"Reference: '{ref_sound['name']}' ({ref_sound['tags']})")
    print("Most similar audio items in dataset:")
    for rank, (score, idx) in enumerate(zip(sim_top.values, sim_top.indices), start=1):
        if idx == ref_idx:
            continue
        s = valid_samples[idx]
        print(f"  #{rank-1} [Similarity: {score.item():.3f}] {s['name']} ({s['tags']})")
    
    return {
        "device": device,
        "sample_count": len(valid_samples),
        "top1_acc": top1_hits / total_q,
        "top3_recall": top3_hits / total_q,
        "passed": target_met
    }

if __name__ == "__main__":
    import urllib.parse
    device = test_hardware()
    samples_dir = Path("E:/SoundReels/data/p0_samples")
    samples = fetch_samples_from_lotsofsounds(samples_dir)
    if len(samples) < 5:
        print("Not enough samples downloaded to run benchmark. Check network connection.")
        sys.exit(1)
    results = benchmark_clap(samples, device=device)
    print(f"\nFinal Results: {json.dumps(results, indent=2)}")
