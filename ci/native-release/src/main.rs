fn main() {
    if omni_release_launcher::process::install_termination_handler().is_err() {
        eprintln!("Native termination cleanup unavailable");
        std::process::exit(1);
    }
    let command = std::env::args().nth(1).unwrap_or_default();
    if command == "check-integration-prerequisites" {
        if std::env::args().len() != 2 {
            eprintln!("Unexpected prerequisite-check arguments");
            std::process::exit(1);
        }
        if let Err(error) =
            omni_release_launcher::prerequisites::check_integration(&std::env::vars().collect())
        {
            eprintln!("Native release preflight: {error}");
            std::process::exit(1);
        }
        return;
    }
    if command == "audit-runners" {
        let args: Vec<String> = std::env::args().skip(2).collect();
        if let Err(error) = omni_release_launcher::runner_policy::cli(&args) {
            eprintln!("Native runner policy: {error}");
            std::process::exit(1);
        }
        return;
    }
    if command == "check-ci-request" {
        let args: Vec<String> = std::env::args().skip(2).collect();
        if let Err(error) = omni_release_launcher::ci_request::check_cli(&args) {
            eprintln!("Native source-CI check: {error}");
            std::process::exit(1);
        }
        return;
    }
    if command == "--help" {
        println!(
            "omni-release-launcher resolve|run|verify-build|agent-run|validate-request|approve-source|agent-source\nomni-release-launcher sign-public-candidates|prepare-publication|publish-candidates|verify-ios|windows-sdk|retain-artifacts\nomni-release-launcher verify-source --root PATH --sha SHA\nomni-release-launcher verify-agent-handoff --directory PATH --version VERSION --platform PLATFORM"
        );
        return;
    }
    if let Err(error) = omni_release_launcher::launch::run(&command) {
        eprintln!("Native release launcher: {error}");
        std::process::exit(1);
    }
}
