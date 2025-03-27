# ProjectionLab Investment Updater

A Docker-based automation tool that updates your ProjectionLab investment accounts with current cryptocurrency and stock values. Inspired by the [sheets2projectionlab project](https://github.com/b-neufeld/sheets2projectionlab).

## Overview

This tool automatically fetches current market prices for cryptocurrencies and stocks, calculates the total value of your holdings, and updates your ProjectionLab investment accounts through their web interface (since they don't have a real public API). It's designed to run as a Docker container and can be scheduled to run periodically.

## Prerequisites

- Docker
- ProjectionLab account with API key

## Usage

### Grab Account IDs from ProjectionLab ([credit](https://github.com/georgeck/projectionlab-monarchmoney-import?tab=readme-ov-file#step-2-get-the-accountid-of-projectionlab-accounts-that-you-want-to-import))

1. Log into ProjectionLab
2. User icon in top-right, Account Settings, Plugins
3. Enable Plugins and note your API Key to use in your configuration
4. Press F12 to open the developer console in your browser (while on the ProjectionLab page), and run the following script that gives you the id and name of your accounts:

    ```javascript
    const exportData = await window.projectionlabPluginAPI.exportData({ key: 'YOUR_PL_API_KEY' });

    // Merge the list of savings accounts, investment accounts, assets and debts
    const plAccounts = [...exportData.today.savingsAccounts, ...exportData.today.investmentAccounts,
                        ...exportData.today.assets, ...exportData.today.debts];

    plAccounts.forEach(account => {
        console.log(account.id, account.name)
    });
    ```

5. Note the information returned by the console to use in your `accounts.yaml`

### Clone this project

```bash
git clone https://github.com/cookandy/projectionlab-investement-updater.git
```

### Update `accounts.yaml`

This file defines your accounts and assets, using the IDs you received via the ProjectionLab page.

1. Copy `accounts-example.yaml` to `accounts.yaml` and update to use your account IDs, friendly names, and assets

    ```yaml
    accounts:
      - id: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxx
        name: Cryptocurrency
        assets:
          crypto:
            bitcoin: 0.23
            ethereum: 10
            litecoin: 4
            cardano: 10
            solana: 5.1
      - id: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxx
        name: Taxable Investments
        assets:
          stock:
            - symbol: AAPL
              shares: 10
            - symbol: NFLX
              shares: 20
      - id: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxx
        name: Mixed Portfolio
        assets:
          crypto:
            bitcoin: 2.3
            ethereum: 10
          stock:
            - symbol: MSFT
              shares: 15
            - symbol: GOOGL
              shares: 3
    ```

### Configuration


### Running the Container

1. Copy `docker-compose-example.yaml` to `docker-compose.yaml`
2. Update any environment variables as needed (see below)
3. From the `projectionlab-investement-updater` directory, build and run the container

    ```bash
    docker compose up --build
    ```

### Environment Variables

You can configure the application using environment variables:

- `PL_USERNAME`: Your ProjectionLab email address
- `PL_PASSWORD`: Your ProjectionLab password
- `PL_API_KEY`: Your ProjectionLab API key
- `PL_MFA_KEY`: Your TOTP secret key (if MFA is enabled)
- `PL_URL`: ProjectionLab login URL (default: `https://app.projectionlab.com/login`)
- `PL_TIME_DELAY`: Seconds to wait for page loading (default: `10`)
- `ACCOUNTS_PATH`: Path to the accounts file (default: `/app/accounts.yaml`)
- `UPDATE_PROJECTIONLAB`: Set to `false` to skip the actual update (for testing) (default: `true`)
- `VALIDATE_ONLY`: Set to `true` to only validate configuration without running updates (default: `false`)
- `RUN_ONCE`: Set to `true` to run the script once and exit (useful for manual runs or custom scheduling)
- `RUN_INTERVAL_MINUTES`: Number of minutes between runs when not using `RUN_ONCE` (default: varies based on implementation)

## How It Works

1. The script loads your account configurations and credentials
2. It fetches current prices for cryptocurrencies and stocks using CoinGecko API and yfinance
3. It calculates the total USD value of your holdings for each account
4. It launches a headless Chrome browser to log into ProjectionLab
5. It handles any MFA authentication if required
6. It uses the ProjectionLab API to update each account with the current balance
7. The browser is closed and the script exits

## Cryptocurrency Caching

The script caches cryptocurrency prices to reduce API calls to CoinGecko. The default cache duration is 300 seconds (5 minutes).

## Lock File System

To prevent concurrent runs, the script uses a lock file at `/tmp/projectionlab_update.lock`. This lock file is automatically cleared after 1 hour if the script crashes.

## Troubleshooting

### Common Issues

- **API Rate Limiting**: If you encounter rate limiting with CoinGecko, the script will automatically retry with exponential backoff
- **MFA Issues**: Ensure your TOTP secret key is correct. The script will attempt to generate a fresh code if the first one fails
- **Browser Automation**: If the script fails to navigate ProjectionLab, try increasing the `PL_TIME_DELAY` value
- **Lock File**: If the script crashes, it may leave a lock file at `/tmp/projectionlab_update.lock`. This prevents concurrent runs and will be automatically cleared after 1 hour
- **Selenium Issues**: The script uses Selenium with Chrome in headless mode. If you encounter issues, check the logs for detailed error messages

## License

[MIT License](LICENSE)
