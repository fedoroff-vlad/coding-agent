package fixture.billing.model;

/** A single billable line on an invoice. */
public class LineItem {

    private final int amount;

    public LineItem(int amount) {
        this.amount = amount;
    }

    public int amount() {
        return amount;
    }
}
