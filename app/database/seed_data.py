from typing import List, Dict, Any

SEED_CATEGORIES: List[Dict[str, Any]] = [
    # ── Tier 1: Core Frontier Tech ──────────────────────────────────────────
    {
        "id": "ai",
        "name": "Artificial Intelligence",
        "icon": "🤖",
        "description": "LLMs, autonomous agents, neural architectures & AI safety.",
        "posts_count": 12,
        "followers_count": 1420,
        "color": "#6366f1"
    },
    {
        "id": "robotics",
        "name": "Robotics & Hardware",
        "icon": "🦾",
        "description": "Humanoid robots, spatial computing, mechatronics and sensors.",
        "posts_count": 8,
        "followers_count": 980,
        "color": "#f59e0b"
    },
    {
        "id": "design",
        "name": "UI/UX & Product Design",
        "icon": "🎨",
        "description": "Micro-interactions, spatial UI, typography, and human-computer interfaces.",
        "posts_count": 10,
        "followers_count": 1150,
        "color": "#ec4899"
    },
    {
        "id": "neuroscience",
        "name": "Neuroscience & BCI",
        "icon": "🧠",
        "description": "Brain-computer interfaces, neural prosthetics, and cognitive models.",
        "posts_count": 6,
        "followers_count": 760,
        "color": "#a855f7"
    },
    {
        "id": "cleantech",
        "name": "CleanTech & Energy",
        "icon": "⚡",
        "description": "Fusion power, solid-state batteries, and carbon removal technologies.",
        "posts_count": 7,
        "followers_count": 840,
        "color": "#22c55e"
    },
    {
        "id": "biotech",
        "name": "Biotech & Longevity",
        "icon": "🧬",
        "description": "CRISPR gene editing, synthetic biology, and cellular rejuvenation.",
        "posts_count": 9,
        "followers_count": 1020,
        "color": "#10b981"
    },
    {
        "id": "space",
        "name": "Space Exploration",
        "icon": "🚀",
        "description": "Orbital logistics, deep space probes, asteroid mining, and habitat engineering.",
        "posts_count": 5,
        "followers_count": 670,
        "color": "#3b82f6"
    },
    {
        "id": "web3",
        "name": "Decentralized Systems",
        "icon": "🌐",
        "description": "Zero-knowledge proofs, decentralized identity, and peer-to-peer compute.",
        "posts_count": 6,
        "followers_count": 590,
        "color": "#f97316"
    },
    # ── Tier 2: Digital & Compute ────────────────────────────────────────────
    {
        "id": "quantum",
        "name": "Quantum Computing",
        "icon": "⚛️",
        "description": "Qubits, quantum error correction, variational algorithms, and photonic chips.",
        "posts_count": 4,
        "followers_count": 510,
        "color": "#06b6d4"
    },
    {
        "id": "cybersecurity",
        "name": "Cybersecurity & Privacy",
        "icon": "🔐",
        "description": "Zero-trust architectures, post-quantum cryptography, and threat intelligence.",
        "posts_count": 5,
        "followers_count": 620,
        "color": "#ef4444"
    },
    {
        "id": "devtools",
        "name": "Developer Tools",
        "icon": "🛠️",
        "description": "IDEs, compilers, CI/CD, observability, and the future of programming.",
        "posts_count": 8,
        "followers_count": 890,
        "color": "#8b5cf6"
    },
    {
        "id": "opensouce",
        "name": "Open Source",
        "icon": "🌍",
        "description": "Community-driven software, governance models, and sustainable open ecosystems.",
        "posts_count": 6,
        "followers_count": 730,
        "color": "#14b8a6"
    },
    {
        "id": "arvr",
        "name": "AR / VR / XR",
        "icon": "🥽",
        "description": "Spatial computing, mixed reality, haptics, and immersive digital worlds.",
        "posts_count": 5,
        "followers_count": 580,
        "color": "#d946ef"
    },
    {
        "id": "gaming",
        "name": "Gaming & Simulation",
        "icon": "🎮",
        "description": "Game AI, physics engines, procedural generation, and esports technology.",
        "posts_count": 4,
        "followers_count": 550,
        "color": "#f43f5e"
    },
    # ── Tier 3: Life Sciences & Health ──────────────────────────────────────
    {
        "id": "healthtech",
        "name": "HealthTech & MedTech",
        "icon": "🏥",
        "description": "Digital diagnostics, AI-assisted surgery, wearable health monitoring.",
        "posts_count": 7,
        "followers_count": 810,
        "color": "#0ea5e9"
    },
    {
        "id": "mentalhealth",
        "name": "Mental Health Tech",
        "icon": "🧘",
        "description": "Digital therapeutics, AI-driven CBT tools, and neurological wellness.",
        "posts_count": 3,
        "followers_count": 430,
        "color": "#a78bfa"
    },
    {
        "id": "longevity",
        "name": "Longevity Science",
        "icon": "⏳",
        "description": "Epigenetic clocks, senolytics, telomere biology, and aging reversal research.",
        "posts_count": 4,
        "followers_count": 490,
        "color": "#fb923c"
    },
    # ── Tier 4: Physical World ───────────────────────────────────────────────
    {
        "id": "nanotech",
        "name": "Nanotechnology",
        "icon": "🔬",
        "description": "Molecular machines, nano-drug delivery, and atomically precise manufacturing.",
        "posts_count": 3,
        "followers_count": 380,
        "color": "#4ade80"
    },
    {
        "id": "materials",
        "name": "Materials Science",
        "icon": "🪨",
        "description": "Metamaterials, 2D crystals, high-entropy alloys, and programmable matter.",
        "posts_count": 3,
        "followers_count": 340,
        "color": "#fbbf24"
    },
    {
        "id": "autonomous",
        "name": "Autonomous Vehicles",
        "icon": "🚗",
        "description": "Self-driving stacks, sensor fusion, V2X communication, and robotaxis.",
        "posts_count": 5,
        "followers_count": 600,
        "color": "#38bdf8"
    },
    {
        "id": "smartcities",
        "name": "Smart Cities",
        "icon": "🏙️",
        "description": "Urban AI, IoT infrastructure, autonomous logistics, and digital twins.",
        "posts_count": 4,
        "followers_count": 470,
        "color": "#60a5fa"
    },
    {
        "id": "wearables",
        "name": "Wearables & Sensors",
        "icon": "⌚",
        "description": "Continuous health monitoring, smart textiles, and body-area networks.",
        "posts_count": 3,
        "followers_count": 390,
        "color": "#34d399"
    },
    # ── Tier 5: Economy & Society ────────────────────────────────────────────
    {
        "id": "fintech",
        "name": "FinTech & DeFi",
        "icon": "💳",
        "description": "Embedded finance, real-time payments, algorithmic trading, and DeFi protocols.",
        "posts_count": 6,
        "followers_count": 690,
        "color": "#fde68a"
    },
    {
        "id": "edtech",
        "name": "EdTech & Learning",
        "icon": "📚",
        "description": "Personalized learning, AI tutors, spaced repetition, and knowledge graphs.",
        "posts_count": 5,
        "followers_count": 560,
        "color": "#86efac"
    },
    {
        "id": "climate",
        "name": "Climate Science",
        "icon": "🌡️",
        "description": "Climate modeling, geoengineering proposals, and carbon accounting systems.",
        "posts_count": 4,
        "followers_count": 480,
        "color": "#6ee7b7"
    },
    {
        "id": "agritech",
        "name": "AgriTech & Food",
        "icon": "🌱",
        "description": "Vertical farming, precision agriculture, lab-grown proteins, and food tech.",
        "posts_count": 3,
        "followers_count": 350,
        "color": "#bbf7d0"
    },
    {
        "id": "oceantech",
        "name": "Ocean & Marine Tech",
        "icon": "🌊",
        "description": "Underwater robotics, ocean energy harvesting, and marine ecosystem monitoring.",
        "posts_count": 2,
        "followers_count": 260,
        "color": "#7dd3fc"
    },
    {
        "id": "supplychain",
        "name": "Supply Chain & Logistics",
        "icon": "🏭",
        "description": "Autonomous warehouses, predictive shipping, and resilient supply networks.",
        "posts_count": 3,
        "followers_count": 310,
        "color": "#d1d5db"
    },
    # ── Tier 6: Philosophy & Culture ─────────────────────────────────────────
    {
        "id": "ethicsai",
        "name": "AI Ethics & Governance",
        "icon": "⚖️",
        "description": "Alignment, bias, copyright, transparency, and the governance of intelligence.",
        "posts_count": 5,
        "followers_count": 530,
        "color": "#fca5a5"
    },
    {
        "id": "philosophy",
        "name": "Philosophy of Mind",
        "icon": "💭",
        "description": "Consciousness, qualia, embodied cognition, and the hard problem.",
        "posts_count": 3,
        "followers_count": 320,
        "color": "#c4b5fd"
    },
    {
        "id": "syntheticmedia",
        "name": "Synthetic Media & AI Art",
        "icon": "🎭",
        "description": "Generative art, deepfakes, AI music, and new creative economies.",
        "posts_count": 4,
        "followers_count": 420,
        "color": "#f9a8d4"
    },
    {
        "id": "futureofwork",
        "name": "Future of Work",
        "icon": "💼",
        "description": "Distributed teams, human-AI collaboration, creator economies, and new labour models.",
        "posts_count": 5,
        "followers_count": 580,
        "color": "#fde68a"
    },
    # ── Tier 7: Creative Expression & Lived Experience ─────────────────────
    # These IDs follow the existing lowercase topic-ID convention so they can
    # be used directly in the existing `topics` and user `interests` lists.
    {
        "id": "poetry",
        "name": "Poetry",
        "icon": "✒️",
        "description": "Poems, verse, spoken word, language, and the craft of expression.",
        "posts_count": 0,
        "followers_count": 0,
        "color": "#a78bfa"
    },
    {
        "id": "literature",
        "name": "Literature",
        "icon": "📖",
        "description": "Books, literary criticism, reading culture, and enduring written works.",
        "posts_count": 0,
        "followers_count": 0,
        "color": "#f59e0b"
    },
    {
        "id": "creativewriting",
        "name": "Creative Writing",
        "icon": "📝",
        "description": "Fiction, essays, storytelling, worldbuilding, and writing practice.",
        "posts_count": 0,
        "followers_count": 0,
        "color": "#ec4899"
    },
    {
        "id": "art",
        "name": "Art",
        "icon": "🖼️",
        "description": "Visual art, illustration, painting, sculpture, and creative process.",
        "posts_count": 0,
        "followers_count": 0,
        "color": "#fb7185"
    },
    {
        "id": "culture",
        "name": "Culture",
        "icon": "🏛️",
        "description": "Communities, customs, ideas, identity, and the changing world around us.",
        "posts_count": 0,
        "followers_count": 0,
        "color": "#f97316"
    },
    {
        "id": "personalexperiences",
        "name": "Personal Experiences",
        "icon": "🌱",
        "description": "Lessons, reflections, life stories, and perspectives from lived experience.",
        "posts_count": 0,
        "followers_count": 0,
        "color": "#14b8a6"
    },
    {
        "id": "music",
        "name": "Music & Sound",
        "icon": "🎵",
        "description": "Composition, performance, listening, sound design, and audio culture.",
        "posts_count": 0,
        "followers_count": 0,
        "color": "#38bdf8"
    },
    {
        "id": "film",
        "name": "Film & Storytelling",
        "icon": "🎬",
        "description": "Cinema, documentary, narrative craft, and visual storytelling.",
        "posts_count": 0,
        "followers_count": 0,
        "color": "#8b5cf6"
    },
    {
        "id": "photography",
        "name": "Photography",
        "icon": "📷",
        "description": "Images, visual narratives, technique, and how we see the world.",
        "posts_count": 0,
        "followers_count": 0,
        "color": "#06b6d4"
    },
]

SEED_POSTS: List[Dict[str, Any]] = [
    {
        "id": "post-ai-01",
        "title": "Continuous Active Inference for Autonomous Coding Agents",
        "text": "What if software development agents didn't rely strictly on discrete chat turns, but ran continuous active inference loops? By constantly predicting the AST diff of the codebase against user intent and minimizing free energy (prediction error), agents could proactively refactor, fix regressions before compile time, and maintain living architectural blueprints. 🧠⚙️",
        "summary": "Proactive active inference loop for autonomous coding agents replacing discrete chat turns.",
        "author_id": "seed-user-dr-elena",
        "author_name": "Dr. Elena Vance",
        "author_handle": "dr_elena",
        "author_photo": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=150&q=80",
        "topics": ["ai", "design"],
        "image_url": None,
        "stats": {
            "likes": 48, "fires": 36, "bulbs": 52,
            "comments": 14, "shares": 19, "views": 420, "hides": 1, "ranking_score": 0.0
        },
        "created_at": "2026-08-15T10:00:00.000Z",
        "updated_at": "2026-08-15T10:00:00.000Z"
    },
    {
        "id": "post-robotics-01",
        "title": "Low-Cost Tendon-Driven Dexterous Hands Using 3D Printed Metamaterials",
        "text": "Current five-finger robotic hands cost upwards of $15,000 due to complex motor gearboxes per joint. By using compliant metamaterial flexures and underactuated tendon cables controlled by just 4 high-torque brushless DC motors, we can achieve 90% of human grasping dexterity for under $350 in bill-of-materials. 🦾🔧",
        "summary": "Under-$350 five-finger compliant robotic hand with metamaterial flexures.",
        "author_id": "seed-user-marcus-chen",
        "author_name": "Marcus Chen",
        "author_handle": "marcus_chen",
        "author_photo": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&w=150&q=80",
        "topics": ["robotics", "design"],
        "image_url": None,
        "stats": {
            "likes": 64, "fires": 42, "bulbs": 28,
            "comments": 9, "shares": 25, "views": 580, "hides": 0, "ranking_score": 0.0
        },
        "created_at": "2026-08-15T14:30:00.000Z",
        "updated_at": "2026-08-15T14:30:00.000Z"
    },
    {
        "id": "post-neuro-01",
        "title": "Non-Invasive EEG + fNIRS Hybrid for Cognitive Load UI Auto-Pacing",
        "text": "Software interfaces should breathe with your cognitive rhythm. When a hybrid EEG/fNIRS headband detects high prefrontal oxygenation and theta oscillations (indicating intense focus or cognitive overload), the OS can automatically silence non-critical notifications, enlarge clickable touch targets, and simplify visual hierarchy. 🧠💡",
        "summary": "Adaptive OS visual hierarchy and notifications based on real-time neural cognitive load.",
        "author_id": "seed-user-dr-kavita",
        "author_name": "Dr. Kavita Sharma",
        "author_handle": "kavita_bci",
        "author_photo": "https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?auto=format&fit=crop&w=150&q=80",
        "topics": ["neuroscience", "design", "ai"],
        "image_url": None,
        "stats": {
            "likes": 39, "fires": 21, "bulbs": 45,
            "comments": 8, "shares": 14, "views": 390, "hides": 0, "ranking_score": 0.0
        },
        "created_at": "2026-08-15T18:15:00.000Z",
        "updated_at": "2026-08-15T18:15:00.000Z"
    },
    {
        "id": "post-cleantech-01",
        "title": "Direct Air Capture Integrated into Concrete Curing Towers",
        "text": "Rather than storing captured CO₂ in underground aquifers with high parasitic pumping costs, we can inject CO₂ directly into precast concrete blocks during industrial curing. The CO₂ permanently mineralizes into calcium carbonate (limestone), locking it away for centuries while increasing the compressive strength of the concrete by 18%. ⚡🌍",
        "summary": "Permanent CO₂ mineralization in concrete curing that boosts compressive strength by 18%.",
        "author_id": "seed-user-lucas-dupont",
        "author_name": "Lucas Dupont",
        "author_handle": "lucas_cleantech",
        "author_photo": "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?auto=format&fit=crop&w=150&q=80",
        "topics": ["cleantech", "climate"],
        "image_url": None,
        "stats": {
            "likes": 55, "fires": 31, "bulbs": 38,
            "comments": 11, "shares": 22, "views": 510, "hides": 0, "ranking_score": 0.0
        },
        "created_at": "2026-08-16T02:00:00.000Z",
        "updated_at": "2026-08-16T02:00:00.000Z"
    },
    {
        "id": "post-design-01",
        "title": "The Death of the Card Grid: Fluid Spatial Canvases for Idea Discovery",
        "text": "For 15 years, social apps trapped ideas in rigid, vertical rectangular cards. Ideas are relational graphs, not trading cards. By transitioning feeds into continuous 2.5D spatial canvases where ideas cluster organically by conceptual cosine similarity, browsing becomes an exploratory journey rather than a mindless slot machine pull. 🎨✨",
        "summary": "Moving from rigid vertical card feeds to relational spatial canvases.",
        "author_id": "seed-user-sophia-martinez",
        "author_name": "Sophia Martinez",
        "author_handle": "sophia_ux",
        "author_photo": "https://images.unsplash.com/photo-1544005313-94ddf0286df2?auto=format&fit=crop&w=150&q=80",
        "topics": ["design", "ai"],
        "image_url": None,
        "stats": {
            "likes": 78, "fires": 54, "bulbs": 61,
            "comments": 23, "shares": 34, "views": 890, "hides": 2, "ranking_score": 0.0
        },
        "created_at": "2026-08-16T06:45:00.000Z",
        "updated_at": "2026-08-16T06:45:00.000Z"
    },
    {
        "id": "post-biotech-01",
        "title": "Cellular Reprogramming with Epigenetic mRNA Cocktails",
        "text": "Instead of using viral vectors to deliver Yamanaka factors (which risks genomic integration and oncogenesis), transient synthetic modified mRNA pulse therapies could partially reset cellular age markers without inducing pluripotent dedifferentiation. Initial in-vitro fibroblast models show 35% reversal of DNA methylation age in 72 hours. 🧬⏳",
        "summary": "Transient mRNA pulse therapy resetting epigenetic age without tumorigenic risk.",
        "author_id": "seed-user-dr-aravind",
        "author_name": "Dr. Aravind Menon",
        "author_handle": "aravind_bio",
        "author_photo": "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?auto=format&fit=crop&w=150&q=80",
        "topics": ["biotech", "longevity"],
        "image_url": None,
        "stats": {
            "likes": 41, "fires": 29, "bulbs": 44,
            "comments": 7, "shares": 16, "views": 360, "hides": 0, "ranking_score": 0.0
        },
        "created_at": "2026-08-16T08:00:00.000Z",
        "updated_at": "2026-08-16T08:00:00.000Z"
    },
    {
        "id": "post-quantum-01",
        "title": "Photonic Quantum Computing Will Beat Superconducting by 2028",
        "text": "Room-temperature photonic qubits using integrated silicon photonics waveguides eliminate the need for dilution refrigerators operating at 15mK. Boson sampling on 100-qubit photonic chips already demonstrates quantum advantage on specific combinatorial optimization tasks. The cost curve is collapsing. ⚛️💡",
        "summary": "Room-temperature photonic chips achieving quantum advantage without dilution refrigerators.",
        "author_id": "seed-user-wei-zhang",
        "author_name": "Wei Zhang",
        "author_handle": "wei_quantum",
        "author_photo": "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?auto=format&fit=crop&w=150&q=80",
        "topics": ["quantum", "ai"],
        "image_url": None,
        "stats": {
            "likes": 33, "fires": 28, "bulbs": 41,
            "comments": 12, "shares": 18, "views": 410, "hides": 0, "ranking_score": 0.0
        },
        "created_at": "2026-08-16T09:00:00.000Z",
        "updated_at": "2026-08-16T09:00:00.000Z"
    },
    {
        "id": "post-space-01",
        "title": "Lunar Regolith 3D Printing for Radiation-Shielded Habitat Modules",
        "text": "NASA's MISSE experiments confirm sintered lunar regolith achieves compressive strength of 75 MPa — stronger than Portland cement — when heated by focused solar concentrators. By using mobile autonomous rovers with robotic extruders, entire habitat shells can be printed from local feedstock before crew arrival, eliminating heavy launch mass for shielding. 🚀🏗️",
        "summary": "Autonomous rovers 3D printing radiation-proof lunar habitats from local regolith.",
        "author_id": "seed-user-priya-nair",
        "author_name": "Priya Nair",
        "author_handle": "priya_space",
        "author_photo": "https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?auto=format&fit=crop&w=150&q=80",
        "topics": ["space", "robotics", "materials"],
        "image_url": None,
        "stats": {
            "likes": 52, "fires": 44, "bulbs": 37,
            "comments": 9, "shares": 21, "views": 480, "hides": 0, "ranking_score": 0.0
        },
        "created_at": "2026-08-16T10:30:00.000Z",
        "updated_at": "2026-08-16T10:30:00.000Z"
    }
]

SEED_COMMENTS: List[Dict[str, Any]] = [
    {
        "id": "comment-ai-01",
        "post_id": "post-ai-01",
        "author_id": "seed-user-marcus-chen",
        "author_name": "Marcus Chen",
        "author_photo": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&w=150&q=80",
        "text": "How do you handle the latency of AST parsing over a million-line monorepo during continuous inference? ❓",
        "comment_type": "question",
        "parent_id": None,
        "likes_count": 8,
        "created_at": "2026-08-15T11:20:00.000Z"
    },
    {
        "id": "comment-ai-02",
        "post_id": "post-ai-01",
        "author_id": "seed-user-dr-elena",
        "author_name": "Dr. Elena Vance",
        "author_photo": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=150&q=80",
        "text": "We scope the active inference attention window to the working module dependency graph and rely on incremental Tree-sitter delta trees. Keeps latency under 40ms ✅",
        "comment_type": "general",
        "parent_id": "comment-ai-01",
        "likes_count": 12,
        "created_at": "2026-08-15T11:45:00.000Z"
    },
    {
        "id": "comment-ai-03",
        "post_id": "post-ai-01",
        "author_id": "seed-user-sophia-martinez",
        "author_name": "Sophia Martinez",
        "author_photo": "https://images.unsplash.com/photo-1544005313-94ddf0286df2?auto=format&fit=crop&w=150&q=80",
        "text": "🟢 Pro: Eliminates the jarring 'prompt-and-wait' barrier. The editor feels like a living collaborator. Love this direction! 🚀",
        "comment_type": "pro",
        "parent_id": None,
        "likes_count": 6,
        "created_at": "2026-08-15T12:10:00.000Z"
    }
]
