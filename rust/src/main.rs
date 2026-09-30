//! swarms-rs benchmark: SwarmsAgent, SequentialWorkflow and ConcurrentWorkflow.
//!
//! Same CLI and result schema as the Python benchmarks in `../python`.

use std::{env, path::PathBuf, time::Instant};

use anyhow::{Context, Result, bail};
use serde::Deserialize;
use serde_json::{Value, json};
use swarms_rs::{
    agent::{SwarmsAgent, SwarmsAgentBuilder},
    llm::provider::anthropic::Anthropic,
    structs::{
        agent::Agent,
        concurrent_workflow::ConcurrentWorkflow,
        conversation::{AgentConversation, Role},
        sequential_workflow::SequentialWorkflow,
    },
};

#[derive(Deserialize, Clone)]
struct AgentSpec {
    name: String,
    system_prompt: String,
}

#[derive(Deserialize)]
struct Single {
    agent: AgentSpec,
    task: String,
}

#[derive(Deserialize)]
struct Multi {
    agents: Vec<AgentSpec>,
    task: String,
}

#[derive(Deserialize)]
struct Tasks {
    single: Single,
    sequential: Multi,
    concurrent: Multi,
}

struct Args {
    scenario: String,
    iterations: usize,
    n_agents: usize,
    out: PathBuf,
}

struct Settings {
    model: Anthropic,
    max_tokens: u64,
}

fn parse_args() -> Result<Args> {
    let mut scenario = None;
    let mut iterations = 1;
    let mut n_agents = 4;
    let mut out = None;
    let mut it = env::args().skip(1);
    while let Some(flag) = it.next() {
        let value = it.next().with_context(|| format!("missing value for {flag}"))?;
        match flag.as_str() {
            "--scenario" => scenario = Some(value),
            "--iterations" => iterations = value.parse()?,
            "--n-agents" => n_agents = value.parse()?,
            "--out" => out = Some(PathBuf::from(value)),
            _ => bail!("unknown flag {flag}"),
        }
    }
    Ok(Args {
        scenario: scenario.context("--scenario is required")?,
        iterations,
        n_agents,
        out: out.context("--out is required")?,
    })
}

fn agent_specs(tasks: &Tasks, scenario: &str, n_agents: usize) -> Vec<AgentSpec> {
    match scenario {
        "coldstart" | "single" => vec![tasks.single.agent.clone()],
        "sequential" => tasks.sequential.agents.clone(),
        _ => {
            let templates = &tasks.concurrent.agents;
            if n_agents <= templates.len() {
                return templates[..n_agents].to_vec();
            }
            (0..n_agents)
                .map(|i| {
                    let t = &templates[i % templates.len()];
                    AgentSpec {
                        name: format!("{}-{i}", t.name),
                        system_prompt: t.system_prompt.clone(),
                    }
                })
                .collect()
        },
    }
}

fn make_agent(settings: &Settings, spec: &AgentSpec) -> SwarmsAgent<Anthropic> {
    // Temperature stays unset: Sonnet 5.5 rejects it. The task-evaluator tool is
    // disabled so, like the other frameworks, each request carries no tools.
    SwarmsAgentBuilder::new_with_model(settings.model.clone())
        .agent_name(&spec.name)
        .system_prompt(&spec.system_prompt)
        .max_loops(1)
        .max_tokens(settings.max_tokens)
        .disable_task_complete_tool()
        .build()
}

fn boxed(settings: &Settings, specs: &[AgentSpec]) -> Vec<Box<dyn Agent>> {
    specs
        .iter()
        .map(|s| Box::new(make_agent(settings, s)) as Box<dyn Agent>)
        .collect()
}

fn assistant_text(conversation: &AgentConversation, last_only: bool) -> String {
    let outputs: Vec<String> = conversation
        .history
        .iter()
        .filter(|m| matches!(m.role, Role::Assistant(_)))
        .map(|m| format!("{}: {}", m.role, m.content))
        .collect();
    if last_only {
        outputs.last().cloned().unwrap_or_default()
    } else {
        outputs.join("\n\n")
    }
}

fn run_record(build_s: f64, run_s: Option<f64>, result: Result<String>) -> Value {
    let (text, error) = match result {
        Ok(text) => (text, None),
        Err(e) => (String::new(), Some(format!("{e:#}"))),
    };
    json!({
        "build_s": build_s,
        "run_s": run_s,
        "ok": error.is_none(),
        "error": error,
        "output_chars": text.chars().count(),
        "output_preview": text.chars().take(400).collect::<String>(),
    })
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = parse_args()?;
    let tasks_path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tasks.json");
    let tasks: Tasks = serde_json::from_str(&std::fs::read_to_string(tasks_path)?)?;

    let base_url = env::var("BENCH_BASE_URL").unwrap_or_else(|_| "http://127.0.0.1:8787".into());
    let model_name = env::var("BENCH_MODEL").unwrap_or_else(|_| "claude-sonnet-5-5".into());
    let api_key = env::var("ANTHROPIC_API_KEY").context("ANTHROPIC_API_KEY is not set")?;
    let max_tokens: u64 = env::var("BENCH_MAX_TOKENS").unwrap_or_else(|_| "4096".into()).parse()?;
    let settings = Settings {
        model: Anthropic::from_url(base_url, api_key).set_model(&model_name),
        max_tokens,
    };
    let metadata_dir = env::temp_dir().join("swarms-rs-bench-metadata");

    let specs = agent_specs(&tasks, &args.scenario, args.n_agents);
    let iterations = if args.scenario == "coldstart" { 1 } else { args.iterations };
    let mut runs = Vec::with_capacity(iterations);

    for _ in 0..iterations {
        let t0 = Instant::now();
        match args.scenario.as_str() {
            "coldstart" => {
                let _agent = make_agent(&settings, &specs[0]);
                runs.push(run_record(t0.elapsed().as_secs_f64(), None, Ok(String::new())));
            },
            "single" => {
                let agent = make_agent(&settings, &specs[0]);
                let build_s = t0.elapsed().as_secs_f64();
                let t1 = Instant::now();
                let result = agent.run(tasks.single.task.clone()).await.map_err(Into::into);
                runs.push(run_record(build_s, Some(t1.elapsed().as_secs_f64()), result));
            },
            "sequential" => {
                let workflow = SequentialWorkflow::builder()
                    .name("BenchSequential")
                    .metadata_output_dir(metadata_dir.to_string_lossy())
                    .agents(boxed(&settings, &specs))
                    .build();
                let build_s = t0.elapsed().as_secs_f64();
                let t1 = Instant::now();
                let result = workflow
                    .run(tasks.sequential.task.clone())
                    .await
                    .map(|c| assistant_text(&c, true))
                    .map_err(Into::into);
                runs.push(run_record(build_s, Some(t1.elapsed().as_secs_f64()), result));
            },
            "concurrent" => {
                let workflow = ConcurrentWorkflow::builder()
                    .name("BenchConcurrent")
                    .agents(boxed(&settings, &specs))
                    .build();
                let build_s = t0.elapsed().as_secs_f64();
                let t1 = Instant::now();
                let result = workflow
                    .run(tasks.concurrent.task.clone())
                    .await
                    .map(|c| assistant_text(&c, false))
                    .map_err(Into::into);
                runs.push(run_record(build_s, Some(t1.elapsed().as_secs_f64()), result));
            },
            other => bail!("unknown scenario {other}"),
        }
    }

    let result = json!({
        "framework": "swarms-rs",
        "version": swarms_rs_version(),
        "scenario": args.scenario,
        "n_agents": specs.len(),
        "model": model_name,
        "max_tokens": max_tokens,
        "import_s": 0.0,
        "runs": runs,
    });
    std::fs::write(&args.out, serde_json::to_string_pretty(&result)?)?;
    Ok(())
}

fn swarms_rs_version() -> &'static str {
    // Resolved from Cargo.lock by build.rs, so it follows whatever Cargo.toml points at.
    env!("SWARMS_RS_VERSION")
}
