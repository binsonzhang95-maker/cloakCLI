mod batch;
mod cookies;
mod cli;
mod client_agent;
mod client_daemon;
mod fleet;
mod jobs;
mod locks;
mod llm;
mod llm_client;
mod master_hub;
mod profiles;
mod protocol;
mod skills;
mod state;
mod teach;
mod teach_optimize;
mod tui;
mod util;
mod worker;

use anyhow::Result;
use clap::Parser;
use cli::{Cli, Commands};

#[tokio::main]
async fn main() -> Result<()> {
    // Reject --api-key *before* clap so the value is never interpolated into
    // "unexpected argument" errors (shell history / process list still saw it).
    llm::reject_api_key_argv(std::env::args())?;
    let cli = Cli::parse();
    let root = state::project_root()?;

    match cli.command {
        None | Some(Commands::Tui) => tui::run(&root).await,
        Some(Commands::Profile { action }) => cli::handle_profile(&root, action).await,
        Some(Commands::Browser { action }) => cli::handle_browser(&root, action).await,
        Some(Commands::Skill { action }) => cli::handle_skill(&root, action).await,
        Some(Commands::Batch { action }) => cli::handle_batch(&root, action).await,
        Some(Commands::Worker { action }) => cli::handle_worker(&root, action).await,
        Some(Commands::Master { action }) => cli::handle_master(&root, action).await,
        Some(Commands::Client { action }) => cli::handle_client(&root, action).await,
        Some(Commands::Fleet { action }) => cli::handle_fleet(&root, action),
        Some(Commands::Llm { action }) => cli::handle_llm(&root, action).await,
        Some(Commands::Teach { action }) => cli::handle_teach(&root, action).await,
        Some(Commands::Doctor) => cli::handle_doctor(&root).await,
    }
}
