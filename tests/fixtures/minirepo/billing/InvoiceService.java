package fixture.billing;

import fixture.billing.model.LineItem;

import java.util.List;

/** Computes invoice totals from line items. */
public class InvoiceService {

    public int total(List<LineItem> items) {
        int sum = 0;
        for (LineItem item : items) {
            sum += item.amount();
        }
        return sum;
    }
}
