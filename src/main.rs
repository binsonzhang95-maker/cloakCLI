mod batch;
mod cookies;
mod cli;
mod client_agent;
mod client_daemon;
mod fleet;
mod jobs;
mod locks;
mod master_hub;
mod profiles;
mod protocol;
mod skills;
mod state;
mod tui;
mod util;
mod worker;

use anyhow::Result;
use clap::Parser;
use cli::{Cli, Commands};

#[tokio::main]
async fn main() -> Result<()> {
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
        Some(Commands::Doctor) => cli::handle_doctor(&root).await,
    }
}
