"""JFT signal engine package."""

# Install the reversal detector onto the existing JFT scanner while keeping
# the scanner lifecycle, historical data source and persistence unchanged.
from app.jft.scanner import JFTScanner
from app.jft.reversal import install_reversal_detection

install_reversal_detection(JFTScanner)
